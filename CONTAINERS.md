# Docker and Kubernetes

## Docker Compose

Requirements: Docker Desktop with Docker Compose.

From the repository root, optionally copy the example environment file and set a private local secret and database password:

```powershell
Copy-Item .env.example .env
```

Edit `.env` before startup. Use URL-safe characters (`A-Z`, `a-z`, `0-9`, `_`, `-`) in `POSTGRES_PASSWORD`, because Compose inserts it into the database connection URL. If `.env` is omitted, the built-in credentials are only for an isolated local test.

Start the app and PostgreSQL:

```powershell
docker compose up --build
```

Open `http://localhost:8000`. Django migrations run when the web container starts. PostgreSQL data and uploaded media are kept in named volumes.

The Compose defaults are for local development only. Set a different `SECRET_KEY` and `POSTGRES_PASSWORD` in a local `.env` file before using this setup beyond your own machine. Do not commit `.env` or use the Compose defaults in a public deployment.

Stop the containers with `docker compose down`. To also delete the local database and uploaded files, use `docker compose down --volumes`.

## Kubernetes (local cluster)

Requirements: a running Kubernetes cluster with a default `ReadWriteOnce` storage class. Docker Desktop Kubernetes can use the locally built image directly. For Minikube or kind, load the image into the cluster after building it.

Build the image:

```powershell
docker build -t eduhope-web:local .
```

Create the namespace and development secrets. This generates fresh values locally and does not add them to the repository:

```powershell
kubectl apply -f k8s/namespace.yaml
$secretKey = py -3 -c "import secrets; print(secrets.token_urlsafe(50))"
$dbPassword = py -3 -c "import secrets; print(secrets.token_urlsafe(32))"
$databaseUrl = "postgresql://eduhope:$dbPassword@postgres:5432/eduhope"
kubectl create secret generic eduhope-secrets --namespace eduhope --dry-run=client -o yaml --from-literal="SECRET_KEY=$secretKey" --from-literal="POSTGRES_PASSWORD=$dbPassword" --from-literal="DATABASE_URL=$databaseUrl" | kubectl apply -f -
```

Deploy the app and database, then forward the service to localhost:

```powershell
kubectl apply -k k8s/
kubectl rollout status statefulset/postgres --namespace eduhope
kubectl rollout status deployment/eduhope-web --namespace eduhope
kubectl port-forward service/eduhope-web 8000:80 --namespace eduhope
```

Open `http://localhost:8000`. The Kubernetes manifests run one web replica and one PostgreSQL instance, with persistent volumes for database data and uploads. They are a local starting point, not a production cluster configuration. For production, use managed PostgreSQL and object storage for uploaded media, move migrations to a controlled release job, configure ingress/TLS, and provide secrets through your cluster's secret manager.

To remove the local Kubernetes resources **and permanently delete their database and uploaded-media volumes**:

```powershell
kubectl delete namespace eduhope
```
