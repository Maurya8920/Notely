"use client";

import React, { useEffect } from "react";
import DatabaseError from "@/components/DatabaseError";

export default function GlobalError({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    useEffect(() => {
        console.error("Unhandled application error:", error);
    }, [error]);

    const isMongoError =
        error.message?.toLowerCase().includes("mongo") ||
        error.message?.toLowerCase().includes("topology") ||
        error.message?.toLowerCase().includes("serverselection");

    return (
        <DatabaseError
            title={isMongoError ? "Database Connection Unavailable" : "Something went wrong"}
            message={
                isMongoError
                    ? "Could not reach the database. Please check your Atlas IP allowlist and network connection, then try again."
                    : "An unexpected error occurred while loading this page. Please try again."
            }
            onRetry={reset}
        />
    );
}
