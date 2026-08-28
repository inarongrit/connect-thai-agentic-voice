import {
  ConsoleLogger,
  DefaultDeviceController,
  DefaultMeetingSession,
  LogLevel,
  MeetingSessionConfiguration,
} from "amazon-chime-sdk-js";

let active = null;

function emit(callback, state, detail = {}) {
  if (typeof callback === "function") callback({ state, ...detail });
}

function stopTracks(stream) {
  if (stream) stream.getTracks().forEach(track => track.stop());
}

export async function startCall({ api, scenario, name, brainMode, audioElement, onState, request }) {
  if (active) await stopCall();
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("browser does not support microphone calling");
  }

  emit(onState, "requesting-microphone");
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: { ideal: true },
      noiseSuppression: { ideal: true },
      autoGainControl: { ideal: true },
      channelCount: { ideal: 1 },
    },
    video: false,
  });

  try {
    emit(onState, "starting-contact");
    const response = await fetch(api, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // `request` lets a caller start a different kind of WebRTC contact — the voice lab
      // posts an action instead of a scenario. The scenario body stays the default so the
      // three demo journeys are unaffected.
      body: JSON.stringify(request || { mode: "webrtc", scenario, name, brainMode: brainMode || "mantle" }),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);

    const logger = new ConsoleLogger("FsiWebRTC", LogLevel.WARN);
    const deviceController = new DefaultDeviceController(logger);
    const configuration = new MeetingSessionConfiguration(
      payload.connectionData.Meeting,
      payload.connectionData.Attendee,
    );
    const meetingSession = new DefaultMeetingSession(configuration, logger, deviceController);
    const session = {
      meetingSession,
      deviceController,
      stream,
      onState,
      contactId: payload.contactId,
      stopped: false,
    };
    active = session;

    meetingSession.audioVideo.addObserver({
      audioVideoDidStartConnecting: reconnecting => {
        emit(onState, reconnecting ? "reconnecting" : "connecting", { contactId: payload.contactId });
      },
      audioVideoDidStart: () => emit(onState, "connected", { contactId: payload.contactId }),
      audioVideoDidStop: status => {
        if (active === session) active = null;
        stopTracks(stream);
        session.stopped = true;
        emit(onState, "ended", {
          contactId: payload.contactId,
          statusCode: status?.statusCode?.(),
        });
      },
    });

    await meetingSession.audioVideo.bindAudioElement(audioElement);
    await meetingSession.audioVideo.startAudioInput(stream);
    meetingSession.audioVideo.start();
    emit(onState, "connecting", { contactId: payload.contactId });
    return { contactId: payload.contactId, statusToken: payload.statusToken };
  } catch (error) {
    stopTracks(stream);
    active = null;
    throw error;
  }
}

export async function stopCall() {
  const session = active;
  if (!session) return;
  active = null;
  if (!session.stopped) session.meetingSession.audioVideo.stop();
  stopTracks(session.stream);
  if (typeof session.deviceController.destroy === "function") {
    await session.deviceController.destroy();
  }
  emit(session.onState, "ended", { contactId: session.contactId, local: true });
}

export function setMuted(muted) {
  if (!active) return false;
  if (muted) active.meetingSession.audioVideo.realtimeMuteLocalAudio();
  else active.meetingSession.audioVideo.realtimeUnmuteLocalAudio();
  return active.meetingSession.audioVideo.realtimeIsLocalAudioMuted();
}

export function isActive() {
  return Boolean(active);
}
