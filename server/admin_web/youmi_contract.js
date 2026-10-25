// Historical responses are normalized at the API boundary during rollout.
export function normalizeYoumiSettings(state) {
  const result = { ...state };
  result.youmi_emotion = result.youmi_emotion || result.mia_emotion;
  delete result.mia_emotion;
  return result;
}
