import os

from waitress import serve

from wsgi import app


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    threads = int(os.environ.get("WAITRESS_THREADS", "8"))
    serve(app, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
