FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
ARG VITE_API_URL=http://localhost:8000/api/v1
ENV VITE_API_URL=$VITE_API_URL
ARG VITE_VERIFY_URL=http://localhost:8001
ENV VITE_VERIFY_URL=$VITE_VERIFY_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /web/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
