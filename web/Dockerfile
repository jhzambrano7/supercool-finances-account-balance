# The demo console's dev image. Development only, on purpose: `web/README.md` is explicit that this
# console is a presentation facility, not a deliverable, so there is no production build target
# here to imply otherwise.
#
# `npm ci` runs at `/`, not at `/app`, so the tree lands in `/node_modules`. Node's resolver walks
# up the parent directories, so code under `/app` finds it, and `npm run` prepends every ancestor
# `node_modules/.bin` to PATH -- meanwhile docker-compose.yml's bind mount over `/app` cannot
# shadow it. Same reasoning as the API image keeping its virtualenv at `/opt/venv`: put the
# installed tree where the mount does not reach, instead of fighting the mount with an anonymous
# volume that silently goes stale whenever the lockfile moves.

FROM node:24-bookworm-slim AS dev

ENV NODE_ENV=development

WORKDIR /
COPY package.json package-lock.json ./
RUN npm ci

WORKDIR /app

EXPOSE 5173
# `--host` binds 0.0.0.0: Vite defaults to loopback, which inside a container means "reachable by
# nothing", including the published port.
CMD ["npm", "run", "dev", "--", "--host"]
