CREATE OR REPLACE VIEW `<firebase-project-id>.game_telemetry.telemetry_gameplay_segments_v1` AS
SELECT *
FROM `<firebase-project-id>.game_telemetry.telemetry_run_summary`
WHERE segmentKind = 'Gameplay';
