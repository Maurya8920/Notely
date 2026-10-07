import { MongoClient, MongoClientOptions } from "mongodb";

declare global {
    var _mongoClientPromise: Promise<MongoClient> | undefined;
}

const uri = process.env.MONGODB_URI;

const options: MongoClientOptions = {
    serverSelectionTimeoutMS: 10000,
    connectTimeoutMS: 10000,
    maxPoolSize: 10,
};

let clientPromise: Promise<MongoClient>;

if (!uri) {
    if (process.env.NODE_ENV === "production") {
        clientPromise = new Promise<MongoClient>((_, reject) => {
            reject(
                new Error(
                    "MONGODB_URI is not defined. Please set MONGODB_URI in your environment variables."
                )
            );
        });
    } else {
        throw new Error("Please add your Mongo URI to .env.local");
    }
} else {
    // Use the global cached client in BOTH dev and production (avoids reconnect storms)
    if (!global._mongoClientPromise) {
        const client = new MongoClient(uri, options);
        global._mongoClientPromise = client.connect().catch((err) => {
            console.error(
                "Cannot connect to MongoDB — check Atlas Network Access IP allowlist and that the cluster is not paused.",
                err
            );
            // Reset cached promise on failure so subsequent requests can retry
            global._mongoClientPromise = undefined;
            throw err;
        });
    }
    clientPromise = global._mongoClientPromise;
}

export default clientPromise;