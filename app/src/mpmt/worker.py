import time, logging
from mpmt.log import setup_logging

log = logging.getLogger("mpmt.worker")

def main():
    setup_logging()
    log.info("worker started (phase 0 stub)")
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
