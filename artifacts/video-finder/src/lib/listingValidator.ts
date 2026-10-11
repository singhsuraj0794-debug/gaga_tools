// The listing-validation logic now lives in the shared workspace package
// @workspace/listing-validation so the API server (Windows) can run the same
// rule-based checks. This file is kept as a thin re-export so existing imports
// (`@/lib/listingValidator`) keep working unchanged.
export * from "@workspace/listing-validation";
