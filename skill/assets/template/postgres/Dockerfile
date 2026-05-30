FROM postgres:18.1-trixie
USER root
RUN groupmod -g 1000 postgres && usermod -u 1000 -g 1000 postgres && chown -R postgres:postgres /run/postgresql
USER postgres
