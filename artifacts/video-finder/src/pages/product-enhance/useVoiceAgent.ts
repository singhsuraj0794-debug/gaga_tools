import { useState, useEffect, useCallback, useRef } from "react";

interface VoiceAgentConfig {
  productName: string;
  currentPrice: number;
  mrp: number;
  minPrice: number;
  language?: "auto" | "en" | "hi";
  onPriceUpdate: (price: number) => void;
  onMessage: (message: { sender: "user" | "agent"; text: string; language?: string }) => void;
  onStatusChange: (status: "idle" | "listening" | "speaking" | "thinking") => void;
}

const WS_URL = "ws://localhost:8000/ws/voice";
const API_URL = "http://localhost:8000";

export function useVoiceAgent(config: VoiceAgentConfig) {
  const {
    productName,
    currentPrice,
    mrp,
    minPrice,
    language = "auto",
    onPriceUpdate,
    onMessage,
    onStatusChange,
  } = config;

  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [isSupported, setIsSupported] = useState(true);
  const [serverStatus, setServerStatus] = useState<"checking" | "online" | "offline">("checking");
  const [status, setStatus] = useState<"idle" | "listening" | "speaking" | "thinking">("idle");
  const [streamingText, setStreamingText] = useState("");

  const wsRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const currentPriceRef = useRef(currentPrice);
  const audioChunksRef = useRef<Blob[]>([]);
  const isRecordingRef = useRef(false);

  useEffect(() => {
    currentPriceRef.current = currentPrice;
  }, [currentPrice]);

  // Check server health
  useEffect(() => {
    const checkServer = async () => {
      try {
        const response = await fetch(`${API_URL}/health`);
        if (response.ok) {
          setServerStatus("online");
          setIsSupported(true);
        } else {
          setServerStatus("offline");
        }
      } catch {
        setServerStatus("offline");
      }
    };

    checkServer();
    const interval = setInterval(checkServer, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleStatusChange = useCallback((newStatus: "idle" | "listening" | "speaking" | "thinking") => {
    setStatus(newStatus);
    onStatusChange(newStatus);
    setIsListening(newStatus === "listening");
    setIsSpeaking(newStatus === "speaking");
  }, [onStatusChange]);

  // WebSocket connection
  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        setIsConnected(true);
        // Send context
        ws.send(JSON.stringify({
          type: "context",
          context: {
            product_name: productName,
            current_price: currentPriceRef.current,
            mrp: mrp,
            min_price: minPrice,
          },
        }));
        ws.send(JSON.stringify({ type: "settings", language }));
      };

      ws.onmessage = async (event) => {
        const data = JSON.parse(event.data);

        switch (data.type) {
          case "transcript":
            onMessage({ sender: "user", text: data.text, language: data.language });
            setStreamingText("");
            break;

          case "llm_stream":
            setStreamingText((prev) => prev + data.token);
            break;

          case "audio_response":
            setStreamingText("");
            onMessage({ sender: "agent", text: data.text, language: data.language });
            handleStatusChange("speaking");

            // Play audio response
            try {
              const audioBytes = atob(data.audio_base64);
              const audioArray = new Uint8Array(audioBytes.length);
              for (let i = 0; i < audioBytes.length; i++) {
                audioArray[i] = audioBytes.charCodeAt(i);
              }

              const audioBlob = new Blob([audioArray], { type: "audio/mpeg" });
              const audioUrl = URL.createObjectURL(audioBlob);
              const audio = new Audio(audioUrl);

              audio.onplay = () => {
                handleStatusChange("speaking");
              };

              audio.onended = () => {
                URL.revokeObjectURL(audioUrl);
                // Resume listening after response
                handleStatusChange("listening");
              };

              audio.onerror = () => {
                URL.revokeObjectURL(audioUrl);
                handleStatusChange("listening");
              };

              // Store audio ref for interruption
              audioChunksRef.current = [];
              await audio.play();
            } catch (err) {
              console.error("Failed to play audio:", err);
              handleStatusChange("listening");
            }
            break;

          case "status":
            handleStatusChange(data.status);
            break;

          case "error":
            console.error("Server error:", data.message);
            break;
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        handleStatusChange("idle");
      };

      ws.onerror = (err) => {
        console.error("WebSocket error:", err);
        setServerStatus("offline");
      };

      wsRef.current = ws;
    } catch (err) {
      console.error("Failed to create WebSocket:", err);
    }
  }, [productName, mrp, minPrice, language, onMessage, handleStatusChange]);

  // Start continuous recording
  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      streamRef.current = stream;
      isRecordingRef.current = true;

      const mediaRecorder = new MediaRecorder(stream, {
        mimeType: "audio/webm;codecs=opus",
      });

      mediaRecorderRef.current = mediaRecorder;

      // Send chunks continuously (every 500ms)
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0 && wsRef.current?.readyState === WebSocket.OPEN) {
          const reader = new FileReader();
          reader.onloadend = () => {
            const base64 = (reader.result as string).split(",")[1];
            if (base64 && wsRef.current?.readyState === WebSocket.OPEN) {
              wsRef.current.send(JSON.stringify({
                type: "audio",
                audio: base64,
                context: {
                  product_name: productName,
                  current_price: currentPriceRef.current,
                  mrp: mrp,
                  min_price: minPrice,
                },
              }));
            }
          };
          reader.readAsDataURL(event.data);
        }
      };

      mediaRecorder.start(500); // Send chunks every 500ms
      handleStatusChange("listening");
    } catch (err) {
      console.error("Failed to start recording:", err);
    }
  }, [productName, mrp, minPrice, handleStatusChange]);

  // Stop recording
  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
    }
    streamRef.current = null;
    isRecordingRef.current = false;
    handleStatusChange("idle");
  }, [handleStatusChange]);

  // Send text message
  const sendText = useCallback(
    async (text: string) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        handleStatusChange("thinking");
        wsRef.current.send(JSON.stringify({
          type: "text",
          text: text,
          context: {
            product_name: productName,
            current_price: currentPriceRef.current,
            mrp: mrp,
            min_price: minPrice,
          },
        }));
      }
    },
    [productName, mrp, minPrice, handleStatusChange]
  );

  // Start agent — connect + start continuous listening
  const startAgent = useCallback(async () => {
    connectWebSocket();
    // Wait for connection
    await new Promise<void>((resolve) => {
      const check = setInterval(() => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          clearInterval(check);
          resolve();
        }
      }, 100);
    });
    // Start continuous recording
    startRecording();
  }, [connectWebSocket, startRecording]);

  // Cleanup
  useEffect(() => {
    return () => {
      if (wsRef.current) wsRef.current.close();
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
      }
    };
  }, []);

  return {
    isListening,
    isSpeaking,
    isConnected,
    isSupported,
    serverStatus,
    status,
    streamingText,
    startListening: startRecording,
    stopListening: stopRecording,
    sendText,
    startAgent,
  };
}
