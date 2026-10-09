FROM node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS node
FROM soulter/astrbot:v4.28.2@sha256:2215f337de16535953df936adaf34a36663d062d85b7ecffe06cdaed74f7b299
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils=25.03.0-5+deb13u4 libpoppler147=25.03.0-5+deb13u4 libgpgme11t64=1.24.2-3 libgpgmepp6t64=1.24.2-3 libnspr4=2:4.36-1 libnss3=2:3.110-1+deb13u4 && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json /opt/notido-cli/
RUN cd /opt/notido-cli && npm ci --ignore-scripts --no-audit --no-fund
COPY requirements.txt /opt/notido-plugin/requirements.txt
RUN uv pip install --require-hashes --system -r /opt/notido-plugin/requirements.txt
COPY . /opt/notido-plugin/
RUN ln -s /opt/notido-cli/node_modules /opt/notido-plugin/node_modules && chmod +x /opt/notido-plugin/deploy/entrypoint.sh
WORKDIR /AstrBot
ENTRYPOINT ["/opt/notido-plugin/deploy/entrypoint.sh"]
