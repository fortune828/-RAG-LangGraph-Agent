
import { motion } from "motion/react";

interface HexagramProps {
  binary: string;
  changingIndices?: number[];
  isChanged?: boolean;
  visibleLines?: number; // Number of lines to show (0-6)
}

export function Hexagram({ binary, changingIndices = [], isChanged = false, visibleLines = 6 }: HexagramProps) {
  // Binary string is from bottom to top (index 0 is 1st line)
  const lines = binary.split("");

  return (
    <div className="flex flex-col-reverse gap-2 w-32 items-center">
      {lines.map((bit, idx) => {
        if (idx >= visibleLines) return <div key={idx} className="w-full h-3" />;
        
        const isChanging = changingIndices.includes(idx);
        return (
          <motion.div
            key={idx}
            initial={{ scaleX: 0, opacity: 0 }}
            animate={{ scaleX: 1, opacity: 1 }}
            transition={{ duration: 0.5 }}
            className="w-full h-3 relative flex justify-center"
          >
            {bit === "1" ? (
              // Yang line (solid)
              <div className={`w-full h-full ${isChanging && !isChanged ? 'bg-amber-500' : 'bg-slate-800'} rounded-sm`} />
            ) : (
              // Yin line (broken)
              <div className="w-full h-full flex justify-between">
                <div className={`w-[45%] h-full ${isChanging && !isChanged ? 'bg-amber-500' : 'bg-slate-800'} rounded-sm`} />
                <div className={`w-[45%] h-full ${isChanging && !isChanged ? 'bg-amber-500' : 'bg-slate-800'} rounded-sm`} />
              </div>
            )}
            {isChanging && !isChanged && (
              <div className="absolute -right-6 top-0 text-amber-600 font-bold text-xs">
                {bit === "1" ? "○" : "×"}
              </div>
            )}
          </motion.div>
        );
      })}
    </div>
  );
}
