import json, logging, sys, time

def setup_logging():
    class JSONFormatter(logging.Formatter):
        def format(self, rec):
            return json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(rec.created)),
                "level": rec.levelname, "logger": rec.name, "msg": rec.getMessage(),
            }, ensure_ascii=False)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JSONFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[h])
