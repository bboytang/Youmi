-- Preserve V9's immutable migration and move new emotion storage to Youmi names.
ALTER TABLE mia_affect_system RENAME TO youmi_affect_system;
ALTER TABLE mia_affect_system_turns RENAME TO youmi_affect_system_turns;
ALTER TABLE mia_affect_system_history RENAME TO youmi_affect_system_history;
DROP INDEX mia_affect_system_history_owner;
CREATE INDEX youmi_affect_system_history_owner ON youmi_affect_system_history(owner,id);
