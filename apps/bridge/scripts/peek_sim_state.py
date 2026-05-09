"""Subscribe to rt/sim_state and rt/rewards_state for a few seconds; print samples."""
import os
import time

os.environ.pop("CYCLONEDDS_URI", None)

from cyclonedds.domain import DomainParticipant  # noqa: E402
from cyclonedds.topic import Topic  # noqa: E402
from cyclonedds.sub import Subscriber, DataReader  # noqa: E402
from cyclonedds.idl import IdlStruct  # noqa: E402
from cyclonedds.idl.types import default  # noqa: E402

# unitree_sim_isaaclab uses std_msgs::dds_::String_ for these.
from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_  # type: ignore  # noqa: E402

DOMAIN = 1
dp = DomainParticipant(DOMAIN)
sub = Subscriber(dp)

for topic_name in ("rt/sim_state", "rt/rewards_state"):
    print(f"\n--- {topic_name} ---")
    topic = Topic(dp, topic_name, String_)
    reader = DataReader(sub, topic)
    deadline = time.time() + 3.0
    seen = 0
    while time.time() < deadline and seen < 3:
        for sample in reader.take(N=10):
            seen += 1
            data = getattr(sample, "data", sample)
            print(f"  sample #{seen}: {repr(data)[:300]}")
            if seen >= 3:
                break
        time.sleep(0.1)
    if seen == 0:
        print("  (no samples in 3s)")
