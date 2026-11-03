# Mercalli Helm chart

Runs the self-hosted Mercalli server: the live API and stream, alerts for the services you depend
on, and the web app. It needs a PostgreSQL database (13 or newer; 17 is what the project tests
with). See [docs/self-hosting.md](../../../docs/self-hosting.md) for the config file reference.

## Install

Put the database URL and any alert secrets in Secrets of your own:

```sh
kubectl create namespace mercalli
kubectl -n mercalli create secret generic mercalli-db \
  --from-literal=DATABASE_URL='postgresql://mercalli:CHANGE_ME@postgres.db.svc:5432/mercalli'
kubectl -n mercalli create secret generic mercalli-alerts \
  --from-literal=slack-webhook-url='https://hooks.slack.com/services/T000/B000/XXXX' \
  --from-literal=pagerduty-routing-key='R0UTINGKEY'
```

Describe your stack and channels in a values file:

```yaml
# my-values.yaml
database:
  existingSecret: mercalli-db
server:
  publicUrl: https://mercalli.example.com
config:
  stack: [github, cloudflare, aws, stripe, openai]
  blast_threshold: 0.3
  channels:
    - type: slack
      name: slack
      url: ${SLACK_WEBHOOK_URL}
      min_impact: minor
    - type: pagerduty
      name: pagerduty
      min_impact: major
      settings:
        routing_key: ${PAGERDUTY_ROUTING_KEY}
secretEnv:
  - name: SLACK_WEBHOOK_URL
    secretName: mercalli-alerts
    secretKey: slack-webhook-url
  - name: PAGERDUTY_ROUTING_KEY
    secretName: mercalli-alerts
    secretKey: pagerduty-routing-key
ingress:
  enabled: true
  className: nginx
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
  hosts:
    - host: mercalli.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: mercalli-tls
      hosts: [mercalli.example.com]
```

Then:

```sh
helm upgrade --install mercalli deploy/helm/mercalli -n mercalli -f my-values.yaml
kubectl -n mercalli rollout status deployment/mercalli
```

## Replicas and upgrades

Every replica serves the API. Only one polls status pages and sends alerts at a time: the
replicas elect it with a PostgreSQL advisory lock, and the others refresh their caches from what
it writes. A rolling update therefore never sends an alert twice; the new pod follows until the
old one exits, then takes over within ten seconds. The server finishes a tick in progress before
it exits (up to 20 seconds), which `terminationGracePeriodSeconds: 45` leaves room for.

## Probes

| Probe | Path | Passes when |
| --- | --- | --- |
| startup, liveness | `/healthz` | the process answers |
| readiness | `/readyz` | the database answers and a tick has completed since the pod started |

## Metrics

`/metrics` is in Prometheus format. With the Prometheus Operator, set `serviceMonitor.enabled`.
The Grafana dashboard in `deploy/grafana/dashboards/mercalli.json` works with either.

## Values

| Key | Default | Meaning |
| --- | --- | --- |
| `image.repository`, `image.tag` | `ghcr.io/dharmeshdev1/mercalli`, appVersion | Image to run |
| `replicaCount` | `1` | API replicas; one of them runs the scheduler |
| `server.interval` | `60` | Seconds between polls of every status page |
| `server.publicUrl` | `""` | URL used in alert links and feeds |
| `server.web` | `true` | Serve the bundled web app at `/` |
| `database.existingSecret`, `database.secretKey` | `""`, `DATABASE_URL` | Secret with the PostgreSQL URL |
| `database.url` | `""` | Have the chart create that Secret instead |
| `config` | small stack, no channels | The config file, as YAML |
| `secretEnv` | `[]` | Environment variables from Secret keys, for `${NAME}` in `config` |
| `envFrom`, `extraEnv` | `[]` | More environment |
| `artifacts.enabled`, `artifacts.volume` | `false`, `{}` | Mount model, graph and census artifacts |
| `resources` | 100m CPU, 256Mi to 1Gi memory | Container resources |
| `ingress.*` | disabled | Ingress; give it a long read timeout for the live stream |
| `serviceMonitor.*` | disabled | Prometheus Operator ServiceMonitor |
| `podSecurityContext`, `securityContext` | non root, read only root filesystem | Pod hardening |
