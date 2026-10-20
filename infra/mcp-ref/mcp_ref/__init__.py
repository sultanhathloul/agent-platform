"""Reference MCP servers of the platform (phase 05, I10, ADR-0211): `routing` over an invented road
graph of public Riyadh landmarks, and `rag` exposing the phase 04 document pipeline. Each runs in
a container of its own on an internal network; the platform reaches them through its MCP client,
behind the broker. They stand in for the owner's servers, which replace them by configuration."""
