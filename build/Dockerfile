# TBT deployment image.
#
# This layers Doppler (for secret injection) on top of a Redash image built from THIS
# fork's source. Because our changes (Slack bot destination, worker cancellation, SSH
# key from env) now live in the source tree, the base image must be built from the repo
# Dockerfile rather than pulled from redash/redash:<tag> and patched. build/build.sh
# builds the base first and passes it in as BASE_IMAGE.
ARG BASE_IMAGE=tbt-redash-base:latest
FROM ${BASE_IMAGE}

# Install the Doppler CLI used to inject secrets at container start.
USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends apt-transport-https ca-certificates curl gnupg && \
    curl -sLf --retry 3 --tlsv1.2 --proto "=https" 'https://packages.doppler.com/public/cli/gpg.DE2A7741A397C129.key' \
      | gpg --dearmor -o /usr/share/keyrings/doppler-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/doppler-archive-keyring.gpg] https://packages.doppler.com/public/cli/deb/debian any-version main" \
      | tee /etc/apt/sources.list.d/doppler-cli.list && \
    apt-get update && \
    apt-get -y install doppler && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
USER redash

# Wrap the stock entrypoint so every process (server/worker/scheduler) runs under
# `doppler run`, which populates the environment (incl. REDASH_SSH_TUNNEL_PRIVATE_KEY)
# from the Doppler project bound to the task.
ENTRYPOINT ["doppler", "run", "--", "/app/bin/docker-entrypoint"]
CMD ["server"]
