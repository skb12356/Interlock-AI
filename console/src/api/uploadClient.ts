import type { UploadedDocument } from "../domain/contracts";
import { ConsoleApiError } from "./consoleClient";

function readFile(file: File): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error("Document could not be read"));
    reader.readAsArrayBuffer(file);
  });
}

function base64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index]);
  }
  return btoa(binary);
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as {
      error?: { message?: string };
      detail?: string;
    };
    return payload.error?.message ?? payload.detail ?? `Upload failed with ${response.status}`;
  } catch {
    return `Upload failed with ${response.status}`;
  }
}

export async function uploadDocument(
  file: File,
  fetcher: typeof fetch = fetch,
): Promise<UploadedDocument> {
  const response = await fetcher("/gateway/v1/uploads", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      filename: file.name,
      content_type: file.type || "application/octet-stream",
      content: base64(await readFile(file)),
      encoding: "base64",
    }),
  });
  if (!response.ok) throw new ConsoleApiError(await errorMessage(response), response.status);
  return (await response.json()) as UploadedDocument;
}
