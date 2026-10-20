# Development baseline only; D will add production static hosting in D-03.
FROM node:22-alpine
WORKDIR /srv
COPY package*.json ./
RUN npm ci
COPY . .
ENV CODERISK_API_TARGET=http://backend:8000
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
