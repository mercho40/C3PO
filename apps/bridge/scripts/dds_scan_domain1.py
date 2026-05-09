"""Focused diagnostic on domain 1 — enumerate writers + readers with topic names."""
import os
import time

os.environ.pop("CYCLONEDDS_URI", None)

from cyclonedds.domain import DomainParticipant  # noqa: E402
from cyclonedds.builtin import (  # noqa: E402
    BuiltinDataReader,
    BuiltinTopicDcpsParticipant,
    BuiltinTopicDcpsPublication,
    BuiltinTopicDcpsSubscription,
)

DOMAIN = 1
print(f"Scanning domain {DOMAIN} for 5 seconds...")
dp = DomainParticipant(DOMAIN)
time.sleep(5)

preader = BuiltinDataReader(dp, BuiltinTopicDcpsParticipant)
parts = list(preader.take(N=50))
print(f"\nParticipants ({len(parts)}):")
for p in parts:
    print(f"  key={getattr(p, 'key', '?')} qos={getattr(p, 'qos', '?')}")

pubreader = BuiltinDataReader(dp, BuiltinTopicDcpsPublication)
pubs = list(pubreader.take(N=200))
print(f"\nPublications ({len(pubs)} writers):")
for w in pubs:
    print(f"  topic={getattr(w, 'topic_name', '?'):40s} type={getattr(w, 'type_name', '?')}")

subreader = BuiltinDataReader(dp, BuiltinTopicDcpsSubscription)
subs = list(subreader.take(N=200))
print(f"\nSubscriptions ({len(subs)} readers):")
for r in subs:
    print(f"  topic={getattr(r, 'topic_name', '?'):40s} type={getattr(r, 'type_name', '?')}")
