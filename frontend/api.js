const JSON_HEADERS = { "Content-Type": "application/json" };

async function unwrap(res) {
  if (res.ok) return res.json();
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch { /* non-JSON error body */ }
  throw new Error(detail);
}

export const get = (path, params) => {
  const qs = params ? "?" + new URLSearchParams(params) : "";
  return fetch(`/api${path}${qs}`).then(unwrap);
};

export const post = (path, body) =>
  fetch(`/api${path}`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body ?? {}) }).then(unwrap);

/**
 * Start a job and poll it to completion.
 * onTick receives {progress, stage} so callers can drive a progress bar.
 */
export async function runJob(path, body, onTick) {
  const started = await post(path, body);
  let delay = 180;

  for (;;) {
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 1.25, 1000);

    const job = await get(`/jobs/${started.id}`);
    onTick?.(job);

    if (job.state === "done") return job.result;
    if (job.state === "error") throw new Error(job.error || "Töö ebaõnnestus");
    if (job.state === "cancelled") throw new Error("Katkestatud");
  }
}
