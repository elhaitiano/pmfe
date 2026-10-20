FROM nginx:stable-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY app/static/ /usr/share/nginx/html/static/
EXPOSE 80
