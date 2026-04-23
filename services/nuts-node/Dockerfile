FROM nutsfoundation/nuts-node:project-gf

USER root

ENV NUTS_CONFIGFILE=/opt/nuts/nuts.yaml

RUN apk add --no-cache ca-certificates su-exec

COPY nuts.yaml /opt/nuts/nuts.yaml.template
COPY policies /opt/nuts/policies
COPY docker-entrypoint.sh /opt/nuts/docker-entrypoint.sh

RUN chmod +x /opt/nuts/docker-entrypoint.sh \
    && mkdir -p /data /usr/local/share/ca-certificates \
    && chown -R 18081:18081 /data /opt/nuts

ENTRYPOINT ["/opt/nuts/docker-entrypoint.sh"]
CMD ["server"]
