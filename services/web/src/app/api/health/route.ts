// Container healthcheck target (docker-compose.yml). Not part of the gateway API.
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ status: "ok" });
}
