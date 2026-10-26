import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import database
from werkzeug.security import generate_password_hash
database.initialize_database()
with database.get_connection() as c:
    for t in ("map_markings","admin_users","incident_reports"): c.execute(f"DELETE FROM {t}")
    c.execute("INSERT INTO admin_users (username,password_hash) VALUES ('admin',?)",(generate_password_hash("correct-horse-battery"),))
    rows=[("Report A","incident",15.0047,120.6487),("MDRRMO Office","facility",14.9990,120.6530),
          ("Gym Shelter","evacuation_center",14.9950,120.6500),("Flood Zone","hazard",15.0100,120.6600),("Plaza","other",15.0000,120.6540)]
    for n,cat,la,lo in rows:
        c.execute("INSERT INTO map_markings (name,category,description,location,latitude,longitude) VALUES (?,?,?,?,?,?)",(n,cat,"<b>desc</b>","Somewhere",la,lo))
print("seeded")
