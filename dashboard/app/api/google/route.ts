// Mailbox connections are deliberately separate from Scout sign-in and disabled in this change.
export async function GET() { return Response.json({ error: "Mailbox connections are not available." }, { status: 403, headers: { "Cache-Control": "private, no-store" } }); }
