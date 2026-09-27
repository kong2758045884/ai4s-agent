import { motion } from "motion/react";
import { DURATION, EASE_OUT, useMotionConfig } from "@/lib/motion";

/**
 * 访客引导加载界面 — 与登录入口保持一致的视觉语言
 */
export default function VisitorBootstrapScreen({ failed = false, onRetry }: { failed?: boolean; onRetry?: () => void }) {
  const { reduce } = useMotionConfig();

  return (
    <div className="relative flex min-h-screen w-full items-center justify-center overflow-hidden">
      {/* 背景层 */}
      <div className="absolute inset-0 bg-[var(--page-gradient)]" />

      {/* 装饰光斑 */}
      <div
        className="pointer-events-none absolute -right-32 top-1/4 h-[500px] w-[500px] rounded-full opacity-60"
        style={{
          background: "radial-gradient(circle, oklch(0.7 0.05 260 / 0.06), transparent 70%)",
          filter: "blur(60px)",
        }}
      />
      <div
        className="pointer-events-none absolute -left-24 bottom-1/4 h-[400px] w-[400px] rounded-full opacity-50"
        style={{
          background: "radial-gradient(circle, oklch(0.65 0.04 200 / 0.05), transparent 70%)",
          filter: "blur(50px)",
        }}
      />

      {/* 内容 */}
      <motion.div
        className="relative z-10 text-center"
        initial={reduce ? { opacity: 0 } : {
          opacity: 0,
          y: 10
        }}
        animate={{
          opacity: 1,
          y: 0
        }}
        transition={{
          duration: reduce ? DURATION.reduced : 0.28,
          ease: EASE_OUT,
        }}
      >
        <h1
          className="mb-2 text-[28px] font-normal leading-[1.15] tracking-tight text-[var(--chat-text)]"
          style={{ fontFamily: "var(--font-display)" }}
        >
          {failed ? "暂时无法连接会话服务" : "正在进入AI原生研判系统"}
        </h1>
        <p className="text-[14px] text-[var(--chat-text-soft)]">
          {failed ? "请重试，或先查看公开的战略图谱。" : "准备 AI4S 研判环境..."}
        </p>
        {failed && <div className="mt-5 flex justify-center gap-4 text-sm">
          <button className="rounded-lg bg-blue-600 px-4 py-2 text-white" onClick={onRetry}>重新连接</button>
          <a className="rounded-lg border px-4 py-2 text-blue-700" href="/workspace/strategic-map">战略图谱</a>
        </div>}
      </motion.div>
    </div>
  );
}
