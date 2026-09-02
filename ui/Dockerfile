FROM node:20-alpine AS build
WORKDIR /s
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /s/dist /usr/share/nginx/html
