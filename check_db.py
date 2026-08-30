import database

res = database.get_expired_sessions(172800)
print(f"Expired sessions returned by get_expired_sessions(172800): {len(res)}")
