"""
Сигналы Django для автоматического создания/обновления связанных объектов
"""
# Message counters are updated atomically by ingestion and outbox services.
# Operator profiles and chat assignments are retained only for schema compatibility;
# the shared queue uses ordinary authenticated Django users.