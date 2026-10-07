import { NextRequest, NextResponse } from "next/server";
import { getCourseIdQuery } from "@/lib/server-utils";
import clientPromise from "@/lib/mongodb";
import { auth0 } from "@/lib/auth0";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

type Mode = "chat" | "ask" | "flashcards";

type CourseDocument = {
    _id: unknown;
    userId: string;
    collaborators?: string[];
    chat: {
        messages: Array<{
            role: "user" | "assistant";
            type: string;
            content?: string;
            fileNames?: string[];
            createdAt: Date;
        }>;
        updatedAt: Date;
    };
};

function error(message: string, status = 400) {
    return NextResponse.json({ error: message }, { status });
}

function extractText(content: unknown): string {
    if (typeof content === "string") return content.trim();
    if (Array.isArray(content)) {
        return content
            .map((block: unknown) => {
                if (typeof block === "object" && block !== null) {
                    const b = block as Record<string, unknown>;
                    if (b.type === "text" && typeof b.text === "string") return b.text;
                }
                return typeof block === "string" ? block : "";
            })
            .join("")
            .trim();
    }
    return String(content ?? "").trim();
}

export async function POST(request: NextRequest) {
    const formData = await request.formData();

    const messageValue = formData.get("message");
    const modeValue = formData.get("mode");
    const userId = formData.get("user_id");
    const courseId = formData.get("course_id");

    const message = typeof messageValue === "string" ? messageValue.trim() : "";
    const mode = modeValue as Mode;

    const files = formData
        .getAll("files")
        .filter((value): value is File => value instanceof File);

    // Validate required fields
    if (!userId || typeof userId !== "string" || !courseId || typeof courseId !== "string") {
        return error("user_id and course_id are required.");
    }

    if (!(["chat", "ask", "flashcards"] as const).includes(mode)) {
        return error("Invalid mode.");
    }

    if (!message && files.length === 0) {
        return error("A message or file is required.");
    }

    const fastApiUrl = (process.env.FASTAPI_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "").replace("localhost", "127.0.0.1");

    // Authentication
    const session = await auth0.getSession();
    const auth0ID = session?.user?.sub.includes("|")
        ? session.user.sub.split("|")[1]
        : session?.user?.sub;
    const userEmail = session?.user?.email;

    // Database
    const client = await clientPromise;
    const courses = client.db().collection<CourseDocument>("courses");

    const course = await courses.findOne(getCourseIdQuery(courseId) as any);
    if (!course) return error("Course not found.", 404);

    const isOwner = course.userId === auth0ID;
    const isCollaborator = Boolean(userEmail && course.collaborators?.includes(userEmail));
    if (!isOwner && !isCollaborator) return error("Access denied to this course.", 403);

    // ── Upload files to FastAPI (parallel for speed) ───────────────────────
    const fileNames = files.map((f) => f.name);
    const uploadResults: unknown[] = [];

    if (files.length > 0) {
        const uploadPromises = files.map(async (file) => {
            const forwardForm = new FormData();
            forwardForm.append("file", file);
            forwardForm.append("user_id", userId);
            forwardForm.append("course_id", courseId);

            const uploadRes = await fetch(`${fastApiUrl}/upload`, {
                method: "POST",
                body: forwardForm,
                signal: AbortSignal.timeout(300_000),
            });

            if (!uploadRes.ok) {
                const text = await uploadRes.text();
                console.error(`[upload] FastAPI /upload failed (${uploadRes.status}):`, text);
                throw new Error(`Upload of "${file.name}" failed: ${text}`);
            }
            return uploadRes.json();
        });

        try {
            const results = await Promise.all(uploadPromises);
            uploadResults.push(...results);
        } catch (e: any) {
            return error(e.message ?? "One or more file uploads failed.", 502);
        }
    }

    // ── Save user message to MongoDB ─────────────────────────────────────
    const now = new Date();
    const existingMessages = course.chat?.messages ?? [];
    const lastMsg = existingMessages[existingMessages.length - 1];
    const isAlreadyAdded = lastMsg && lastMsg.role === "user" && lastMsg.content === message && !files.length;

    if (!isAlreadyAdded) {
        const userDbMessage: Record<string, unknown> = {
            role: "user",
            type: mode,
            createdAt: now,
        };
        if (message) userDbMessage.content = message;
        if (fileNames.length > 0) userDbMessage.fileNames = fileNames;

        await courses.updateOne(
            getCourseIdQuery(courseId) as any,
            { $push: { "chat.messages": userDbMessage as any }, $set: { "chat.updatedAt": now } } as any
        );
    }

    // ── File-only upload — return upload confirmation, no AI call needed ──
    if (!message) {
        const confirmationMessage = {
            role: "assistant",
            type: "upload_confirmation",
            content: fileNames.length === 1
                ? `✅ **${fileNames[0]}** has been uploaded and indexed successfully. You can now ask questions about it.`
                : `✅ **${fileNames.length} files** uploaded and indexed successfully:\n${fileNames.map((n) => `- ${n}`).join("\n")}\n\nYou can now ask questions about them.`,
            createdAt: new Date(),
        };

        await courses.updateOne(
            getCourseIdQuery(courseId) as any,
            {
                $push: { "chat.messages": confirmationMessage as any },
                $set: { "chat.updatedAt": confirmationMessage.createdAt },
            } as any
        );

        return NextResponse.json({
            message: confirmationMessage,
            uploads: uploadResults,
            fileNames,
        });
    }

    // ── Build history ────────────────────────────────────────────────────
    const history = (course.chat?.messages ?? [])
        .filter((m) => m.content && typeof m.content === "string" && m.content.trim())
        .map((m) => ({ role: m.role as "user" | "assistant", content: m.content!.trim() }));

    // ── Check if course has uploaded documents ────────────────────────────
    const allCourseMessages = course.chat?.messages ?? [];
    const uploadedFileNames: string[] = [];
    for (const msg of allCourseMessages) {
        if (Array.isArray(msg.fileNames)) {
            uploadedFileNames.push(...msg.fileNames);
        }
    }
    if (fileNames.length > 0) {
        uploadedFileNames.push(...fileNames);
    }
    const hasUploadedDocs = uploadedFileNames.length > 0;
    const latestFileName = uploadedFileNames.length > 0
        ? uploadedFileNames[uploadedFileNames.length - 1]
        : undefined;

    // ── Call FastAPI directly ─────────────────────────────────────────────
    // If mode is "chat" and course has documents, automatically add retrieved context via /chat-with-context
    const endpoint =
        mode === "flashcards"
            ? "/generate-flashcards"
            : mode === "ask"
                ? "/ask"
                : hasUploadedDocs
                    ? "/chat-with-context"
                    : "/chat";

    const body =
        mode === "flashcards"
            ? { query: message, user_id: userId, course_id: courseId, num_cards: 5, ...(latestFileName ? { document_name: latestFileName } : {}) }
            : mode === "ask"
                ? { query: message, user_id: userId, course_id: courseId, ...(latestFileName ? { document_name: latestFileName } : {}) }
                : hasUploadedDocs
                    ? { query: message, user_id: userId, course_id: courseId, history, ...(latestFileName ? { document_name: latestFileName } : {}) }
                    : { query: message, history };

    let generation: Record<string, unknown>;
    try {
        let res = await fetch(`${fastApiUrl}${endpoint}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            signal: AbortSignal.timeout(300_000),
        });

        // Graceful fallback: If /chat-with-context returns 404, fall back to /ask
        if (!res.ok && res.status === 404 && endpoint === "/chat-with-context") {
            res = await fetch(`${fastApiUrl}/ask`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: message, user_id: userId, course_id: courseId, ...(latestFileName ? { document_name: latestFileName } : {}) }),
                signal: AbortSignal.timeout(300_000),
            });
        }

        if (!res.ok) {
            const text = await res.text();
            console.error(`[chat] FastAPI ${endpoint} failed (${res.status}):`, text);
            return error(`FastAPI ${endpoint} failed (${res.status}): ${text}`, 502);
        }

        generation = (await res.json()) as Record<string, unknown>;
    } catch (e: any) {
        console.error(`[chat] Could not reach FastAPI at ${fastApiUrl}${endpoint}:`, e?.message ?? e);
        return error(`Could not reach the AI service: ${e?.message ?? "Connection refused or timed out"}. Is the FastAPI backend running?`, 502);
    }

    // ── Build assistant message ────────────────────────────────────────────
    const responseAt = new Date();
    const assistantMessage: Record<string, unknown> = {
        role: "assistant",
        type: mode === "flashcards" ? "flashcards" : mode === "ask" ? "answer" : "chat",
        createdAt: responseAt,
    };

    if (mode === "flashcards") {
        assistantMessage.flashcards = Array.isArray(generation.flashcards) ? generation.flashcards : [];
    } else {
        const rawContent =
            generation.answer ?? generation.message ?? generation.response ?? generation.content ?? generation;
        assistantMessage.content =
            typeof rawContent === "string" ? rawContent : extractText(rawContent);
        if (Array.isArray(generation.sources) && generation.sources.length > 0) {
            assistantMessage.sources = generation.sources;
        }
    }

    // ── Save assistant message to MongoDB ──────────────────────────────────
    await courses.updateOne(
        getCourseIdQuery(courseId) as any,
        {
            $push: { "chat.messages": assistantMessage as any },
            $set: { "chat.updatedAt": responseAt },
        } as any
    );

    return NextResponse.json({
        message: assistantMessage,
        uploads: uploadResults,
        fileNames,
    });
}