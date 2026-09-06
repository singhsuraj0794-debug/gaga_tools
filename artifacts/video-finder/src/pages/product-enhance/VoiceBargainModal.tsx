import { useState, useRef, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Input } from "@/components/ui/input";
import {
  Mic,
  MicOff,
  Phone,
  PhoneOff,
  Volume2,
  VolumeX,
  AlertCircle,
  Loader2,
  Send,
  Wifi,
  WifiOff,
  Globe,
} from "lucide-react";
import type { Product } from "./products";
import { useVoiceAgent } from "./useVoiceAgent";

interface VoiceBargainModalProps {
  open: boolean;
  onClose: () => void;
  product: Product;
}

type CallStatus = "idle" | "connecting" | "connected" | "ended";
type Language = "auto" | "en" | "hi";

const LANGUAGES = [
  { code: "auto", label: "Auto Detect", flag: "🌐" },
  { code: "en", label: "English", flag: "🇬🇧" },
  { code: "hi", label: "हिन्दी", flag: "🇮🇳" },
];

export default function VoiceBargainModal({
  open,
  onClose,
  product,
}: VoiceBargainModalProps) {
  const [callStatus, setCallStatus] = useState<CallStatus>("idle");
  const [currentOffer, setCurrentOffer] = useState(product.price);
  const [messages, setMessages] = useState<
    Array<{ sender: "user" | "agent"; text: string; language?: string }>
  >([]);
  const [textInput, setTextInput] = useState("");
  const [selectedLanguage, setSelectedLanguage] = useState<Language>("auto");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const addMessage = (msg: {
    sender: "user" | "agent";
    text: string;
    language?: string;
  }) => {
    setMessages((prev) => [...prev, msg]);
  };

  const updatePrice = (price: number) => {
    setCurrentOffer(price);
  };

  const {
    isListening,
    isSpeaking,
    isConnected,
    isSupported,
    serverStatus,
    status,
    streamingText,
    startListening,
    stopListening,
    sendText,
    startAgent,
  } = useVoiceAgent({
    productName: product.name,
    currentPrice: product.price,
    mrp: product.mrp,
    minPrice: Math.floor(product.price * 0.7),
    onPriceUpdate: updatePrice,
    onMessage: addMessage,
    onStatusChange: () => {},
    language: selectedLanguage,
  });

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const startCall = () => {
    setCallStatus("connecting");
    setMessages([]);
    setCurrentOffer(product.price);

    setTimeout(() => {
      setCallStatus("connected");
      startAgent();
    }, 1500);
  };

  const endCall = () => {
    stopListening();
    setCallStatus("ended");
    setMessages((prev) => [
      ...prev,
      {
        sender: "agent",
        text:
          selectedLanguage === "hi"
            ? `Aapka samay ke liye dhanyavaad! Aakhri offer ₹${currentOffer} hai. Checkout kar sakte hain.`
            : `Thank you for your time! The final offer is ₹${currentOffer}. You can proceed with checkout.`,
        language: selectedLanguage === "hi" ? "hi" : "en",
      },
    ]);
  };

  const handleTextSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (textInput.trim()) {
      addMessage({ sender: "user", text: textInput });
      sendText(textInput);
      setTextInput("");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="text-orange-500">🎤</span>
            Voice Bargain
            <Badge variant="outline" className="ml-2 text-xs">
              Multi-Language
            </Badge>
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {/* Product Info */}
          <div className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg">
            <img
              src={product.image}
              alt={product.name}
              className="w-16 h-16 object-cover rounded"
              onError={(e) => {
                e.currentTarget.src =
                  "https://placehold.co/64/f3f4f6/374151?text=Img";
              }}
            />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-slate-900 truncate">
                {product.name}
              </p>
              <div className="flex items-center gap-2">
                <span className="text-lg font-bold">₹{currentOffer}</span>
                <span className="text-sm text-slate-500 line-through">
                  ₹{product.mrp}
                </span>
              </div>
            </div>
          </div>

          {/* Language Selector */}
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-slate-500" />
            <div className="flex gap-1">
              {LANGUAGES.map((lang) => (
                <Button
                  key={lang.code}
                  variant={
                    selectedLanguage === lang.code ? "default" : "outline"
                  }
                  size="sm"
                  onClick={() => setSelectedLanguage(lang.code as Language)}
                  className="text-xs"
                >
                  {lang.flag} {lang.label}
                </Button>
              ))}
            </div>
          </div>

          {/* Server Status */}
          <div className="flex items-center gap-2 text-xs">
            {serverStatus === "checking" && (
              <>
                <Loader2 className="h-3 w-3 animate-spin text-slate-400" />
                <span className="text-slate-500">Checking server...</span>
              </>
            )}
            {serverStatus === "online" && (
              <>
                <Wifi className="h-3 w-3 text-green-500" />
                <span className="text-green-600">Local server connected</span>
              </>
            )}
            {serverStatus === "offline" && (
              <>
                <WifiOff className="h-3 w-3 text-red-500" />
                <span className="text-red-600">
                  Server offline - Run: python voice-agent/start.py
                </span>
              </>
            )}
          </div>

          {/* Error removed — errors shown via serverStatus */}

          {/* Call Status */}
          {callStatus === "connecting" && (
            <div className="text-center py-8">
              <div className="w-16 h-16 bg-blue-100 rounded-full flex items-center justify-center mx-auto mb-4 animate-pulse">
                <Loader2 className="h-8 w-8 text-blue-600 animate-spin" />
              </div>
              <p className="text-slate-600">
                {selectedLanguage === "hi"
                  ? "AI agent se jud rahe hain..."
                  : "Connecting to AI agent..."}
              </p>
              <p className="text-xs text-slate-400 mt-2">
                Qwen2.5 7B + Whisper + Edge TTS
              </p>
            </div>
          )}

          {callStatus === "connected" && (
            <>
              {/* Status Badge */}
              <div className="text-center">
                <Badge
                  variant="outline"
                  className={
                    status === "listening"
                      ? "bg-green-50 text-green-700 border-green-300"
                      : status === "speaking"
                      ? "bg-blue-50 text-blue-700 border-blue-300"
                      : status === "thinking"
                      ? "bg-amber-50 text-amber-700 border-amber-300"
                      : "bg-slate-50 text-slate-700 border-slate-300"
                  }
                >
                  <span
                    className={`w-2 h-2 rounded-full mr-2 ${
                      status === "listening"
                        ? "bg-green-500 animate-pulse"
                        : status === "speaking"
                        ? "bg-blue-500 animate-pulse"
                        : status === "thinking"
                        ? "bg-amber-500 animate-pulse"
                        : "bg-slate-400"
                    }`}
                  />
                  {status === "listening"
                    ? selectedLanguage === "hi"
                      ? "Sun rahe hain..."
                      : "Listening..."
                    : status === "speaking"
                    ? selectedLanguage === "hi"
                      ? "Agent bol raha hai..."
                      : "Speaking..."
                    : status === "thinking"
                    ? selectedLanguage === "hi"
                      ? "Soch raha hai..."
                      : "Thinking..."
                    : selectedLanguage === "hi"
                    ? "Taiyar"
                    : "Ready"}
                </Badge>
              </div>

              {/* Chat Messages */}
              <ScrollArea className="h-48">
                <div className="space-y-3 p-3">
                  {messages.map((msg, i) => (
                    <div
                      key={i}
                      className={`flex ${
                        msg.sender === "user" ? "justify-end" : "justify-start"
                      }`}
                    >
                      <div
                        className={`max-w-[85%] p-2 rounded-lg text-sm ${
                          msg.sender === "user"
                            ? "bg-blue-500 text-white"
                            : "bg-white border text-slate-700"
                        }`}
                      >
                        {msg.text}
                        {msg.language && (
                          <span className="text-[10px] opacity-60 ml-1">
                            {msg.language === "hi" ? "🇮🇳" : "🇬🇧"}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                  {/* Streaming LLM text */}
                  {streamingText && (
                    <div className="flex justify-start">
                      <div className="max-w-[85%] p-2 rounded-lg text-sm bg-white border text-slate-700 border-amber-200 bg-amber-50">
                        {streamingText}
                        <span className="inline-block w-1 h-3 bg-amber-500 ml-1 animate-pulse" />
                      </div>
                    </div>
                  )}
                  <div ref={messagesEndRef} />
                </div>
              </ScrollArea>

              {/* Text Input */}
              <form onSubmit={handleTextSubmit} className="flex gap-2">
                <Input
                  value={textInput}
                  onChange={(e) => setTextInput(e.target.value)}
                  placeholder={
                    selectedLanguage === "hi"
                      ? "Apna offer type karein..."
                      : "Type your offer or message..."
                  }
                />
                <Button type="submit" size="icon" disabled={!textInput.trim()}>
                  <Send className="h-4 w-4" />
                </Button>
              </form>

              {/* Voice Controls */}
              <div className="flex items-center justify-center gap-3">
                <Button
                  variant={status === "listening" ? "destructive" : "outline"}
                  size="lg"
                  onClick={() => {
                    if (status === "listening") {
                      stopListening();
                    } else {
                      startListening();
                    }
                  }}
                >
                  {status === "listening" ? (
                    <MicOff className="h-5 w-5" />
                  ) : (
                    <Mic className="h-5 w-5" />
                  )}
                </Button>

                <Button
                  size="lg"
                  className={`w-16 h-16 rounded-full ${
                    status === "listening"
                      ? "bg-red-500 hover:bg-red-600"
                      : "bg-green-500 hover:bg-green-600"
                  }`}
                  onClick={() => {
                    if (status === "listening") {
                      stopListening();
                    } else {
                      startListening();
                    }
                  }}
                >
                  {status === "listening" ? (
                    <MicOff className="h-6 w-6" />
                  ) : (
                    <Mic className="h-6 w-6" />
                  )}
                </Button>

                <Button variant="destructive" size="lg" onClick={endCall}>
                  <PhoneOff className="h-5 w-5" />
                </Button>
              </div>

              <p className="text-xs text-center text-slate-500">
                {status === "listening"
                  ? selectedLanguage === "hi"
                    ? "Sun rahe hain... Ab bole"
                    : "Listening... Speak now"
                  : status === "speaking"
                  ? selectedLanguage === "hi"
                    ? "Agent bol raha hai..."
                    : "Agent is speaking..."
                  : status === "thinking"
                  ? selectedLanguage === "hi"
                    ? "Soch raha hai..."
                    : "AI is thinking..."
                  : selectedLanguage === "hi"
                  ? "Mic par click karein ya type karein"
                  : "Click microphone or type to speak"}
              </p>
            </>
          )}

          {callStatus === "ended" && (
            <div className="text-center py-6 space-y-4">
              <div className="w-16 h-16 bg-orange-100 rounded-full flex items-center justify-center mx-auto">
                <span className="text-2xl">💰</span>
              </div>
              <div>
                <p className="text-lg font-bold text-slate-900">
                  {selectedLanguage === "hi" ? "Aakhri Offer" : "Final Offer"}: ₹
                  {currentOffer}
                </p>
                <p className="text-sm text-slate-500">
                  {selectedLanguage === "hi"
                    ? `Aap ₹${product.mrp - currentOffer} bacha rahe hain (${Math.round(
                        ((product.mrp - currentOffer) / product.mrp) * 100
                      )}% off)`
                    : `You save ₹${product.mrp - currentOffer} (${Math.round(
                        ((product.mrp - currentOffer) / product.mrp) * 100
                      )} off)`}
                </p>
              </div>
              <div className="flex gap-3 justify-center">
                <Button variant="outline" onClick={onClose}>
                  {selectedLanguage === "hi" ? "Band Karein" : "Close"}
                </Button>
                <Button className="bg-green-500 hover:bg-green-600">
                  {selectedLanguage === "hi"
                    ? "Checkout Karein"
                    : "Proceed to Checkout"}
                </Button>
              </div>
            </div>
          )}

          {callStatus === "idle" && (
            <div className="text-center py-8">
              <div className="w-20 h-20 bg-gradient-to-br from-orange-400 to-amber-500 rounded-full flex items-center justify-center mx-auto mb-4">
                <Mic className="h-10 w-10 text-white" />
              </div>
              <p className="text-slate-600 mb-2">
                {selectedLanguage === "hi"
                  ? "Bolkar ya likhkar price negotiate karein"
                  : "Start a voice conversation to bargain for the best price"}
              </p>
              <p className="text-xs text-slate-400 mb-4">
                {selectedLanguage === "hi"
                  ? "Hindi • English • Hinglish support"
                  : "Hindi • English • Hinglish support"}
              </p>

              {serverStatus === "offline" ? (
                <div className="space-y-2">
                  <p className="text-sm text-amber-600 font-medium">
                    {selectedLanguage === "hi"
                      ? "Pehle voice agent server start karein:"
                      : "Start the voice agent server first:"}
                  </p>
                  <code className="block text-xs bg-slate-100 p-2 rounded text-slate-700">
                    cd voice-agent && python start.py
                  </code>
                </div>
              ) : (
                <Button
                  size="lg"
                  className="bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600"
                  onClick={startCall}
                >
                  <Phone className="h-5 w-5 mr-2" />
                  {selectedLanguage === "hi"
                    ? "Voice Bargain Shuru Karein"
                    : "Start Voice Bargain"}
                </Button>
              )}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
