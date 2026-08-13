# Reccy Usage

Recs, Showco, Twitcho, and Lyte are the direct editable clients of Reccy. This
table records the current integration boundary for each project.

| Client | Reccy facilities | Current use | Deliberate boundary |
| --- | --- | --- | --- |
| Recs | `Reccy`, `rpc`, `service`, `models`, `paths`, `renderers`, `ipc`, `logging`, `settings` | `ExternalServer` derives from `Reccy` and provides the external control and event RPC endpoints. Service installation and status delegate to Reccy while retaining Recs-specific metadata and status fields. Recs uses atomic JSON writes for saved settings and daemon status. | The GUI protocol remains Recs-specific. It uses Reccy's low-level IPC transport, not Reccy RPC, so its messages and behavior are unchanged. |
| Showco | `cli`, `Reccy`, `rpc.ClientAdapter`, `service`, `models`, `paths`, `process`, `subprocess`, `ipc` | The top-level command dispatcher uses `reccy.cli`. `ShowcoDaemon` derives from `Reccy` for service installation. `TwitchoClient` derives from `rpc.ClientAdapter`. X18 recording and Twitcho supervision use shared process lifecycle helpers; provisioning, network configuration, and updates use the subprocess wrapper. | Showco's Recs adapter uses the Recs GUI protocol over low-level IPC because that protocol is internal to Recs. Showco does not expose its HTTP UI through daemon RPC. |
| Twitcho | `Reccy`, `rpc`, `logging`, `process` | Its configuration derives from `Reccy`. The streaming control endpoint is a Reccy RPC server. Startup logging and FFmpeg stderr capture, failure reporting, and termination use Reccy helpers. Video utility scripts use `process.run_silent`. | Twitcho currently exposes RPC but has no Reccy RPC client, so `rpc.ClientAdapter` is not applicable. |
| Lyte | `Reccy`, `ReccyStatus`, `rpc`, `service`, `models`, `paths`, `renderers`, `logging` | `LyteMidiDaemon` derives from `Reccy`, supplies a custom status model, installs and controls its daemon service, and exposes RPC commands for status, blackout, stop, and patch selection. Lyte uses Reccy logging across the application. | Lyte currently exposes RPC but has no Reccy RPC client, so `rpc.ClientAdapter` is not applicable. |

The reusable facilities remain independently available. A project can adopt one
of them directly, or derive from `Reccy` to combine daemon services, optional
RPC, logging, and persisted settings.
