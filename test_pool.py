import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from dotenv import load_dotenv
import os

load_dotenv()
url = os.environ.get("DATABASE_URL")
pool = ThreadedConnectionPool(1, 2, url)

c1 = pool.getconn()
print("Got c1")
pool.putconn(c1, close=True)
print("Closed c1 and returned to pool")

c2 = pool.getconn()
print("Got c2")
c2.close() # manually break it
try:
    with c2.cursor() as cur:
        cur.execute("SELECT 1")
except Exception as e:
    print("Caught error:", type(e))
    pool.putconn(c2, close=True)
    c3 = pool.getconn()
    print("Got c3")
    pool.putconn(c3)
