from django.db import migrations

# History of every accepted point, kept for 7 days.
# - Partitioned by day so retention is a DROP of the oldest partition.
# - The primary key doubles as the idempotency key (a child cannot be in two places at
#   the same instant, so a repeated update hits the conflict) and as the index for
#   reading one child's track in time order. It must include the partition key.
# - No foreign keys on purpose: this is the hottest write path (about 1.3k inserts/s).
CREATE_SQL = """
CREATE TABLE location_updates (
    child_id    bigint           NOT NULL,
    device_id   bigint           NOT NULL,
    recorded_at timestamptz      NOT NULL,
    latitude    double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude   double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    received_at timestamptz      NOT NULL,
    PRIMARY KEY (child_id, recorded_at)
) PARTITION BY RANGE (recorded_at);
"""


class Migration(migrations.Migration):
    dependencies = [('tracking', '0001_initial')]

    operations = [
        migrations.RunSQL(CREATE_SQL, reverse_sql='DROP TABLE location_updates;'),
    ]
