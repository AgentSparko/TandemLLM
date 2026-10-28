# Fleet API contract v1

These files are the truth for `/v1/fleet/*`, the API of TandemApp's controller: the Sparks it manages, the links between them, the deployments, per-node metrics, memory, events, alerts, rules and the saved window layouts (SRV-723, SRV-725, SRV-729, SRV-731, VIS-256). The dashboard's mock fleet (`dashboard/mock/fleet.ts`) validates every answer against them (`npm run test:contract` in `dashboard/`); the controller's tests will hold its real responses to the same files. They live beside `dashboard-v1` so the two contracts can change on their own.

| file | endpoint |
|-|-|
| `nodes.schema.json` | `GET /v1/fleet/nodes`: every paired Spark with its alias, identity colour, transport, status and inventory |
| `node.schema.json` | `GET /v1/fleet/nodes/{id}`: one node (404 for an id the fleet does not have); the answer of its `PATCH` too |
| `node-patch.schema.json` | the body of `PATCH /v1/fleet/nodes/{id}`: `alias`, `colour`, `icon`, `tags`, any of them; answers the node as changed |
| `links.schema.json` | `GET /v1/fleet/links`: management (`lan`), remote (`wan`) and `fabric` links; a fabric link carries its last test, RoCE, its logical interfaces and the switch port |
| `topology.schema.json` | `GET /v1/fleet/topology`: the controller host, the infrastructure between it and the nodes (LAN, internet, gateway, switch), the nodes, the links and one group per deployment (drawn as a hull over its members) |
| `deployments.schema.json` | `GET /v1/fleet/deployments`: `single`, `replicas`, `split` (with its stages in pipeline order) and `role` (with `role_of`) |
| `metrics.schema.json` | `GET /v1/fleet/metrics?source=all\|node:<id>\|deployment:<id>&metric=<key>&range=5m\|1h\|6h\|24h\|7d` |
| `memory.schema.json` | `GET /v1/fleet/memory`: the unified memory split per node in GiB, with the engine's ceiling, the 24 h peak and the usable total |
| `events.schema.json` | `GET /v1/fleet/events?since=<t>`: the fleet timeline, newest first, only events after `since` when it is given |
| `alerts.schema.json` | `GET /v1/fleet/alerts`: the alerts firing now, acknowledged or not |
| `alert.schema.json` | `POST /v1/fleet/alerts/{id}/ack`: the alert after the acknowledgement (404 for an alert that does not fire) |
| `rules.schema.json` | `GET /v1/fleet/rules` |
| `logs.schema.json` | `GET /v1/fleet/logs?node=all\|<id>&level=debug\|info\|warn\|error&q=<text>&limit=<n>`: the latest lines of the nodes' logs (each node's agent and engines), oldest first; `level` keeps that level and the ones above it |
| `layout.schema.json` | `GET /v1/fleet/layouts/{page}?device=desk\|tablet\|phone` and the body of its `PUT`, which answers the layout as saved |
| `pair.schema.json` | the body of `POST /v1/fleet/pair`: `{address, code, port?}`, pairing by the 6-digit code the node shows; answers the node as paired (`node.schema.json`) |
| `pair-ssh.schema.json` | the body of `POST /v1/fleet/pair/ssh`: SSH once with a password or a key for this call only; answers the node as paired |
| `hostkey.schema.json` | `POST /v1/fleet/pair/ssh/hostkey {host, port?}`: the host key the node offers, `new`, `same` or `changed` against the one this controller remembers |
| `release.schema.json` | `DELETE /v1/fleet/nodes/{id}[?force=1]`: the node forgets this controller, then the controller forgets the node |
| `trust.schema.json` | `GET /v1/fleet/trust`: this controller's certificate and the audit of pairings, take-overs and releases |
| `session.schema.json` | `GET /v1/fleet/session`: whether an admin password is set and the browser is signed in; `POST {token}` signs in (204 and the session cookie), `DELETE` signs out |
| `error.schema.json` | every error body |

The schemas use the same JSON Schema 2020-12 subset as `dashboard-v1`; each file is self-contained. `additionalProperties` is `false` everywhere except a window's `ref`, which holds the window kind's own keys (a node, a file, a metric).

## Rules the schemas cannot express

- Times are Unix seconds. A metric answer's sample `i` is at `t0 + i * step_s`; the last sample is at or before the request. `step_s` is the range's span over its sample count less one (5m: 151 samples, 1h: 121, 6h: 145, 24h: 145, 7d: 169); the end sits on a 30 s grid, or on the step when the step is finer.
- The controller sends every metric per node and never aggregates: the page computes Total, Stacked, Lines, Split and Table from `series` with `agg`. `sum` metrics add up (tok/s, requests, power, memory, disk, network), `mean` and `max` do not, `maxmean` (temperatures) shows the max with the mean dashed. A `null` value is no sample (the node was off or not yet paired, or has no fabric port): aggregates skip it, they never count it as zero.
- `source=all` gives one series per paired node, `node:<id>` one, `deployment:<id>` one per member; with a deployment, `tps` and `req` count that deployment's tokens and requests only, the hardware metrics the whole member. A Split's tokens count once per stage at half, so the members add up to the deployment's total. `cap` is the per-node capacity of the metric (`mem`: the usable unified memory), else `null`.
- A node's `colour` is its identity, 1 to 8: 1 to 4 are the four node hues, 5 to 8 the same hues drawn hollow and dashed. Node ids are stable (certificate fingerprint and DMI serial); an address or tunnel port may change, the id does not.
- A `PATCH` of a node is refused (400) for an alias another node has (letter case aside) or a colour another node has: the alias and the colour name one node each. A new node gets the first colour no node has.
- A node's `status` is never only a colour in the UI: `up` ●, `draining` ◐, `off` ○, `alert` ⚠, each with `status_text`.
- An event's and an alert's `node` is a node id, `all` (the whole fleet) or `host` (the controller's machine).
- A layout is one page on one device class (VIS-256). A `PUT` stores the body with `saved_at` set to the controller's clock, whatever the body said: the last write wins. A window's `at` is its dock (`right`, `bottom`), `float` or `min`; `prev` is where a minimised one returns to; `x`, `y`, `w`, `h` place it while it floats (pixels in the page's work area) and `z` orders the floating ones. A dock's `f` is its share of the work area, `act` the window its tabs show. `saved_at` is null for the default a page answers before its first save; the page then lays out its own default windows.
- A node's `pairing` (SEC-4): `local` is the controller's own machine, `paired` a node whose certificate the controller pins (`via` `code` or `ssh`, `fingerprint` the pinned certificate's SHA-256, `host_key` the SSH host key an SSH pairing confirmed), `unpaired` a node record without trust yet (`off`, "not paired"). Pairing, release and take-over need the admin password (a session from `/v1/fleet/session` or the bearer token), or a caller on the controller's own machine; with no password set, only the latter.
- Pairing errors: 403 `wrong_code` (also when a certificate was swapped on the way: the two look alike on purpose), 410 `code_spent` (expired, used, or five wrong codes), 409 `owned` (another TandemApp owns the node: take it over on the node, or `takeover: true` over SSH), 409 `conflict`, 409 `host_key_changed`, 409 `not_installed`, 502 `unreachable` and `ssh_failed`, 429 `too_many` (ten attempts a minute).
- Metric keys: `tps` decode tok/s (sum), `req` active requests (sum), `ttft` TTFT p50 ms (mean), `gpu` GPU % (mean), `power` W (sum), `temp` GPU °C (maxmean), `sm` and `memclk` MHz (mean), `mem` unified memory used GiB (sum), `cpu` % (mean), `disk` MB/s (sum), `lan` MB/s (sum), `fabric` Gb/s (sum), `rtt` ms from the controller (max).

## Changes

- v1.0, first version (VIS-244): the routes above, served by the dashboard's mock before the controller exists.
- v1.0, `GET` and `PATCH /v1/fleet/nodes/{id}` (SRV-723): a node's alias, colour, icon and tags, set by the user; served by tandem-app (`proxy/fleet.py`) and the mock.
- v1.0, node trust (SEC-4): a node's `pairing`; `POST /v1/fleet/pair`, `/pair/ssh`, `/pair/ssh/hostkey`, `DELETE /v1/fleet/nodes/{id}`, `GET /v1/fleet/trust`, `/v1/fleet/session`; the error types of pairing.
