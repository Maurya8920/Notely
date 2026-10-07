"use client";

import React from "react";
import { Database, RefreshCw, Home } from "lucide-react";
import Link from "next/link";

interface DatabaseErrorProps {
    title?: string;
    message?: string;
    onRetry?: () => void;
}

export default function DatabaseError({
    title = "Database Connection Unavailable",
    message = "We are currently having trouble connecting to the database. Please verify your connection or try again shortly.",
    onRetry,
}: DatabaseErrorProps) {
    const handleReload = () => {
        if (onRetry) {
            onRetry();
        } else {
            window.location.reload();
        }
    };

    return (
        <div className="flex min-h-[60vh] flex-col items-center justify-center bg-background px-6 py-12 text-center text-foreground">
            <div className="flex w-full max-w-md flex-col items-center">
                <div className="mb-6 flex h-14 w-14 items-center justify-center rounded-2xl border border-destructive/20 bg-destructive/10 text-destructive shadow-xs">
                    <Database className="h-6 w-6" />
                </div>

                <h1 className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
                    {title}
                </h1>
                <p className="mt-3 text-sm leading-relaxed text-muted-foreground sm:text-base">
                    {message}
                </p>

                <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
                    <button
                        type="button"
                        onClick={handleReload}
                        className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-primary px-5 text-sm font-medium text-primary-foreground shadow-xs transition-opacity hover:opacity-90"
                    >
                        <RefreshCw className="h-4 w-4" />
                        <span>Try Again</span>
                    </button>

                    <Link
                        href="/"
                        className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-border bg-card px-5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    >
                        <Home className="h-4 w-4" />
                        <span>Home</span>
                    </Link>
                </div>

                <p className="mt-6 text-xs text-muted-foreground/80">
                    If this persists, please check your network connection and MongoDB Atlas access.
                </p>
            </div>
        </div>
    );
}
