"use client";

import { useState } from "react";
import { Sparkles, ChevronDown, Loader2, Flame } from "lucide-react";

const STYLES = [
  { value: "Cinematic", label: "Cinematic", desc: "Epic film quality" },
  { value: "Realistic", label: "Realistic", desc: "True-to-life" },
  { value: "Anime", label: "Anime", desc: "Japanese animation" },
  { value: "Fantasy", label: "Fantasy", desc: "Magical & fantastical" },
  { value: "Documentary", label: "Documentary", desc: "Journalistic style" },
];

const MODES = [
  {
    value: "idea2video",
    label: "Idea to Video",
    desc: "Full agentic pipeline: story, scenes, storyboard, video",
  },
  {
    value: "script2video",
    label: "Script to Video",
    desc: "Start from your own scene script",
  },
];

const GENRES = [
  { value: "billionaire_revenge", label: "💰 Billionaire Revenge", desc: "Humiliated maid buys the empire" },
  { value: "rejected_mate", label: "🐺 Rejected Mate", desc: "Alpha rejects fated mate, she awakens" },
  { value: "time_loop", label: "⏰ Time-Loop Murder", desc: "Dies at midnight, wakes at 8am" },
  { value: "fake_heiress", label: "👑 Fake Heiress", desc: "Impostor is the real heir" },
  { value: "ai_husband", label: "🤖 AI Husband", desc: "Perfect husband knows too much" },
  { value: "murder_mystery", label: "🔪 Murder Mystery", desc: "Victim names the killer, then dies" },
];

const FLAGSHIP_IDEA =
  "THE MAID WHO BOUGHT THE EMPIRE — Ep 1: A maid is slapped and fired on camera at a gala for stealing a necklace she didn't steal. Her phone rings: 'Ma'am, acquisition complete. You now own 51% of Vega Group.' The CEO kneels. She takes the company. Cliffhanger: 'Now... let's talk about who burned my house down.'";

export default function IdeaForm({ onSubmit, isSubmitting }) {
  const [idea, setIdea] = useState("");
  const [userRequirement, setUserRequirement] = useState("");
  const [style, setStyle] = useState("Cinematic");
  const [mode, setMode] = useState("idea2video");
  const [script, setScript] = useState("");
  const [retentionMode, setRetentionMode] = useState(true);
  const [genre, setGenre] = useState("billionaire_revenge");
  const [episodeNumber, setEpisodeNumber] = useState(1);
  const [seriesBible, setSeriesBible] = useState("");

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!idea.trim() && (mode !== "script2video" || !script.trim())) return;
    onSubmit({
      idea: idea.trim(),
      user_requirement: userRequirement.trim(),
      style,
      mode,
      script: script.trim(),
      retention_mode: retentionMode,
      genre,
      episode_number: Number(episodeNumber) || 1,
      series_bible: seriesBible.trim(),
    });
  };

  const inputStyle = {
    backgroundColor: "#12121a",
    border: "1px solid #22223a",
    borderRadius: "12px",
    color: "#e2e8f0",
    padding: "12px 16px",
    width: "100%",
    fontSize: "14px",
    transition: "border-color 0.2s",
    outline: "none",
    resize: "vertical",
  };

  const labelStyle = {
    display: "block",
    fontSize: "13px",
    fontWeight: "500",
    marginBottom: "8px",
    color: "#94a3b8",
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-2xl p-6 space-y-6"
      style={{
        backgroundColor: "#12121a",
        border: "1px solid #1a1a26",
      }}
    >
      {/* Hooked Series toggle */}
      <div
        className="p-4 rounded-xl"
        style={{
          backgroundColor: retentionMode ? "rgba(249, 115, 22, 0.08)" : "#1a1a26",
          border: retentionMode ? "1px solid rgba(249, 115, 22, 0.45)" : "1px solid #22223a",
        }}
      >
        <button
          type="button"
          onClick={() => setRetentionMode(!retentionMode)}
          className="w-full flex items-center gap-3 text-left"
          style={{ cursor: "pointer", background: "none", border: "none" }}
        >
          <span
            className="w-10 h-10 rounded-full flex items-center justify-center shrink-0"
            style={{ backgroundColor: retentionMode ? "rgba(249,115,22,0.2)" : "#22223a" }}
          >
            <Flame size={18} style={{ color: retentionMode ? "#fb923c" : "#6b7280" }} />
          </span>
          <span className="flex-1">
            <span className="block text-sm font-semibold" style={{ color: retentionMode ? "#fdba74" : "#e2e8f0" }}>
              🔥 Hooked Series Mode — High Retention
            </span>
            <span className="block text-xs mt-0.5" style={{ color: "#6b7280" }}>
              Cold-open hook (3s) • Open loop • Escalation every 5s • Cliffhanger ending • Hook audit
            </span>
          </span>
          <span
            className="w-11 h-6 rounded-full relative shrink-0 transition-all"
            style={{ backgroundColor: retentionMode ? "#f97316" : "#374151" }}
          >
            <span
              className="absolute top-0.5 w-5 h-5 rounded-full bg-white transition-all"
              style={{ left: retentionMode ? "22px" : "2px" }}
            />
          </span>
        </button>

        {retentionMode && (
          <div className="mt-4 space-y-4">
            <div>
              <label style={labelStyle}>Viral Genre Engine</label>
              <div className="grid grid-cols-2 gap-2">
                {GENRES.map((g) => (
                  <button
                    key={g.value}
                    type="button"
                    onClick={() => setGenre(g.value)}
                    className="text-left p-3 rounded-xl transition-all"
                    style={{
                      backgroundColor: genre === g.value ? "rgba(249,115,22,0.15)" : "#12121a",
                      border: genre === g.value ? "1px solid rgba(249,115,22,0.55)" : "1px solid #22223a",
                      cursor: "pointer",
                    }}
                  >
                    <div className="text-xs font-semibold" style={{ color: genre === g.value ? "#fdba74" : "#e2e8f0" }}>
                      {g.label}
                    </div>
                    <div className="text-xs mt-0.5" style={{ color: "#6b7280", fontSize: "11px" }}>{g.desc}</div>
                  </button>
                ))}
              </div>
            </div>
            <div className="flex gap-3">
              <div className="flex-1">
                <label style={labelStyle}>Episode #</label>
                <input
                  type="number"
                  min="1"
                  max="50"
                  value={episodeNumber}
                  onChange={(e) => setEpisodeNumber(e.target.value)}
                  style={inputStyle}
                />
              </div>
              <div className="flex-[2]">
                <label style={labelStyle}>Series Bible (optional — paste Ep 1 bible for continuity)</label>
                <input
                  type="text"
                  value={seriesBible}
                  onChange={(e) => setSeriesBible(e.target.value)}
                  placeholder="Paste previous bible JSON/text..."
                  style={inputStyle}
                />
              </div>
            </div>
            <button
              type="button"
              onClick={() => { setIdea(FLAGSHIP_IDEA); setGenre("billionaire_revenge"); setEpisodeNumber(1); setMode("idea2video"); }}
              className="w-full py-2.5 rounded-xl text-xs font-semibold transition-all"
              style={{
                backgroundColor: "rgba(249,115,22,0.12)",
                border: "1px solid rgba(249,115,22,0.4)",
                color: "#fdba74",
                cursor: "pointer",
              }}
            >
              ⚡ Load Flagship Series: “The Maid Who Bought the Empire” — Ep 1
            </button>
          </div>
        )}
      </div>

      {/* Mode selector */}
      <div>
        <label style={labelStyle}>Generation Mode</label>
        <div className="grid grid-cols-2 gap-3">
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              onClick={() => setMode(m.value)}
              className="text-left p-4 rounded-xl transition-all"
              style={{
                backgroundColor: mode === m.value ? "rgba(124, 58, 237, 0.15)" : "#1a1a26",
                border: mode === m.value
                  ? "1px solid rgba(124, 58, 237, 0.5)"
                  : "1px solid #22223a",
                cursor: "pointer",
              }}
            >
              <div
                className="text-sm font-semibold mb-1"
                style={{ color: mode === m.value ? "#a78bfa" : "#e2e8f0" }}
              >
                {m.label}
              </div>
              <div className="text-xs" style={{ color: "#6b7280" }}>
                {m.desc}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Idea input */}
      <div>
        <label style={labelStyle}>
          {mode === "script2video" ? "Brief idea or title" : "Your Idea *"}
        </label>
        <textarea
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          placeholder={
            mode === "idea2video"
              ? retentionMode
                ? "e.g. A maid is fired on camera... then buys the company that fired her. Ep 1 cliffhanger: 'Who burned my house down?'"
                : "e.g. A lone astronaut discovers an ancient alien structure on Mars..."
              : "Brief title or concept for your video"
          }
          rows={3}
          required={mode === "idea2video"}
          style={inputStyle}
          onFocus={(e) => (e.target.style.borderColor = "#7c3aed")}
          onBlur={(e) => (e.target.style.borderColor = "#22223a")}
        />
      </div>

      {/* Script input (script2video only) */}
      {mode === "script2video" && (
        <div>
          <label style={labelStyle}>Scene Script *</label>
          <textarea
            value={script}
            onChange={(e) => setScript(e.target.value)}
            placeholder="Write your scene script here. Tip: open with a shocking line, end on a cliffhanger..."
            rows={6}
            required
            style={inputStyle}
            onFocus={(e) => (e.target.style.borderColor = "#7c3aed")}
            onBlur={(e) => (e.target.style.borderColor = "#22223a")}
          />
        </div>
      )}

      {/* Style selector */}
      <div>
        <label style={labelStyle}>Visual Style</label>
        <div className="grid grid-cols-5 gap-2">
          {STYLES.map((s) => (
            <button
              key={s.value}
              type="button"
              onClick={() => setStyle(s.value)}
              className="p-3 rounded-xl text-center transition-all"
              style={{
                backgroundColor: style === s.value ? "rgba(124, 58, 237, 0.15)" : "#1a1a26",
                border: style === s.value
                  ? "1px solid rgba(124, 58, 237, 0.5)"
                  : "1px solid #22223a",
                cursor: "pointer",
              }}
            >
              <div
                className="text-xs font-semibold mb-1"
                style={{ color: style === s.value ? "#a78bfa" : "#e2e8f0" }}
              >
                {s.label}
              </div>
              <div className="text-xs" style={{ color: "#4b5563", fontSize: "10px" }}>
                {s.desc}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Additional requirements */}
      <div>
        <label style={labelStyle}>
          Additional Requirements{" "}
          <span style={{ color: "#4b5563", fontWeight: 400 }}>(optional)</span>
        </label>
        <textarea
          value={userRequirement}
          onChange={(e) => setUserRequirement(e.target.value)}
          placeholder={
            retentionMode
              ? "e.g. Vertical 9:16 feel, fast cuts, hateable villain, revenge payoff in Ep 1..."
              : "Any specific instructions, mood, pacing, color palette, character details..."
          }
          rows={2}
          style={inputStyle}
          onFocus={(e) => (e.target.style.borderColor = "#7c3aed")}
          onBlur={(e) => (e.target.style.borderColor = "#22223a")}
        />
      </div>

      {/* Submit */}
      <button
        type="submit"
        disabled={isSubmitting || (!idea.trim() && (mode !== "script2video" || !script.trim()))}
        className="w-full py-4 rounded-xl text-sm font-semibold flex items-center justify-center gap-2 transition-all"
        style={{
          background: isSubmitting
            ? "#3b3b5c"
            : retentionMode
              ? "linear-gradient(135deg, #f97316 0%, #ea580c 100%)"
              : "linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%)",
          color: isSubmitting ? "#6b7280" : "white",
          cursor: isSubmitting ? "not-allowed" : "pointer",
          boxShadow: isSubmitting ? "none" : retentionMode ? "0 4px 20px rgba(249,115,22,0.4)" : "0 4px 20px rgba(124, 58, 237, 0.4)",
        }}
      >
        {isSubmitting ? (
          <>
            <Loader2 size={16} className="animate-spin" />
            Starting pipeline...
          </>
        ) : (
          <>
            <Sparkles size={16} />
            {retentionMode ? "Generate Hooked Episode 🔥" : "Generate Video"}
          </>
        )}
      </button>
    </form>
  );
}
