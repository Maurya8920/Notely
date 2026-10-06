import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
    const fastApiUrl = (process.env.FASTAPI_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "").replace("localhost", "127.0.0.1");

    try {
        const start = Date.now();
        const res = await fetch(`${fastApiUrl}/ping`, {
            cache: "no-store",
            signal: AbortSignal.timeout(10000),
        });
        const latencyMs = Date.now() - start;

        return NextResponse.json({
            status: res.ok ? "healthy" : `status_${res.status}`,
            service: "fastapi",
            latencyMs,
            timestamp: new Date().toISOString(),
        });
    } catch (err: any) {
        return NextResponse.json(
            {
                status: "unreachable",
                service: "fastapi",
                error: err.message,
                timestamp: new Date().toISOString(),
            },
            { status: 503 }
        );
    }
}
