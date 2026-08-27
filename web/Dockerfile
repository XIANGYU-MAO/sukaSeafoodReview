FROM node:22-alpine AS build

WORKDIR /web
COPY package.json package-lock.json ./
ARG SUKASEAFOOD_NPM_REGISTRY=https://registry.npmmirror.com
RUN sed -i \
      -e "s#https://registry.npmjs.org#${SUKASEAFOOD_NPM_REGISTRY%/}#g" \
      -e "s#https://registry.npmmirror.com#${SUKASEAFOOD_NPM_REGISTRY%/}#g" \
      package-lock.json \
    && npm ci --registry="${SUKASEAFOOD_NPM_REGISTRY}"
COPY . .
RUN npm run typecheck && npm test && npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=nginx:nginx /web/dist /usr/share/nginx/html
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=5 \
    CMD ["wget", "--quiet", "--tries=1", "--spider", "http://127.0.0.1:8080/healthz"]
