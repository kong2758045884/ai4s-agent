import classNames from "classnames";
import { motion } from "motion/react";

import FeaturedConversationCard from "@/components/FeaturedConversationCard";
import GeneralInput from "@/components/GeneralInput";
import { AnimatedOrb } from "@/components/chat/AnimatedOrb";
import { KeyboardTypewriter } from "@/components/ai-elements/keyboard-typewriter";
import type { FeaturedConversationCard as FeaturedConversationCardModel } from "@/services/featuredConversation";
import { DURATION, EASE_OUT, useMotionConfig } from "@/lib/motion";
import Ai4sDailyToday from "./Ai4sDailyToday";
import type { Ai4sDailyHomeHotspot } from "@/utils/ai4sDailyHome";

const HERO_TYPEWRITER_TEXTS = [
  "探索 AI4S 前沿",
  "研判科研趋势",
  "分析技术演进",
  "连接科学与智能",
  "AI4S 研判系统",
];

export default function WelcomeView(props: {
  currentConversation: CHAT.ConversationHistory;
  product: CHAT.Product;
  visitorUsername?: string;
  videoModalOpen?: string;
  featuredCards: FeaturedConversationCardModel[];
  onSelectionChange: (selection: {
    product: CHAT.Product;
    deepThink: boolean;
  }) => void;
  onSend: (inputInfo: CHAT.TInputInfo) => void;
  onResearchHotspot?: (hotspot: Ai4sDailyHomeHotspot) => void;
  onOpenVideo: (url: string) => void;
  onCloseVideo: () => void;
  onOpenFeaturedConversations?: () => void;
  onOpenFeaturedDetail?: (featuredId: string) => void;
}) {
  const hasFeaturedCards = props.featuredCards.length > 0;
  const { reduce } = useMotionConfig();

  return (
    <div className="h-full w-full overflow-hidden px-6 md:px-12 lg:px-16">
      <div
        className={classNames(
          "mx-auto flex min-h-full w-full max-w-[1280px] flex-col items-center py-4 lg:py-5",
          hasFeaturedCards ? "justify-start" : "justify-center"
        )}
      >
        <div
          className={classNames(
            "flex w-full flex-col items-center",
            // 欢迎态内容保持紧凑，避免首页在常规窗口高度下出现纵向滚动。
            hasFeaturedCards ? "pt-5 md:pt-6 lg:pt-8" : "pt-6 md:pt-8 lg:pt-10"
          )}
        >
          <div className="mb-5 text-center lg:mb-6">
            <div className="orb-intro mx-auto mb-4 flex justify-center">
              <AnimatedOrb size={88} />
            </div>
            <h1
              className="text-blur-intro mb-3 text-[32px] font-medium leading-[1.08] tracking-normal text-[var(--chat-text)] md:text-[42px] lg:text-[48px]"
              style={{ fontFamily: "var(--font-sans)" }}
            >
              <KeyboardTypewriter
                texts={HERO_TYPEWRITER_TEXTS}
                speed={80}
                eraseSpeed={45}
                holdMs={10000}
                pauseMs={550}
              />
            </h1>
          </div>

          <Ai4sDailyToday onResearchHotspot={props.onResearchHotspot} />

          <motion.div
            initial={
              reduce
                ? { opacity: 0 }
                : {
                  opacity: 0,
                  y: 12,
                  scale: 0.98,
                }
            }
            animate={{
              opacity: 1,
              y: 0,
              scale: 1,
            }}
            transition={{
              duration: reduce ? DURATION.reduced : 0.28,
              delay: reduce ? 0 : 0.08,
              ease: EASE_OUT,
            }}
            className="mb-5 w-full max-w-[920px] lg:mb-6"
          >
            <div className="w-full">
              <GeneralInput
                key={`welcome-input-${props.currentConversation.sessionId}`}
                sessionId={props.currentConversation.sessionId}
                placeholder={props.product.placeholder}
                showBtn={true}
                size="big"
                disabled={false}
                product={props.product}
                deepThink={props.currentConversation.deepThink}
                send={props.onSend}
                onSelectionChange={props.onSelectionChange}
              />
            </div>
          </motion.div>
        </div>

        {hasFeaturedCards ? (
          <motion.section
            initial={reduce ? { opacity: 0 } : {
              opacity: 0,
              y: 10
            }}
            animate={{
              opacity: 1,
              y: 0,
            }}
            transition={{
              duration: reduce ? DURATION.reduced : 0.28,
              delay: reduce ? 0 : 0.12,
              ease: EASE_OUT,
            }}
            className="mx-auto mt-4 w-full max-w-[1180px] pb-20"
          >
            <div className="mb-5 flex items-end justify-between gap-4">
              <div>
                <h2 className="text-[22px] font-semibold tracking-tight text-[var(--chat-text)]">
                  精品对话
                </h2>
                <p className="mt-1 text-[13px] text-[var(--chat-text-muted)]">
                  精选公开案例，点击查看完整回放
                </p>
              </div>
              <button
                type="button"
                onClick={() => props.onOpenFeaturedConversations?.()}
                className="inline-flex h-9 items-center gap-1.5 rounded-full border border-[var(--chat-border)] bg-[var(--chat-surface)] px-3.5 text-[13px] font-medium text-[var(--chat-text-soft)] transition hover:text-[var(--chat-text)]"
              >
                <span>查看全部</span>
                <i className="font_family icon-xinjianjiantou text-[10px]" />
              </button>
            </div>

            {/* 精品对话始终走公共只读路由，避免和访客自己的会话状态耦合。 */}
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {props.featuredCards.map((card) => (
                <FeaturedConversationCard
                  key={card.featuredId}
                  card={card}
                  variant="grid"
                  onSelect={props.onOpenFeaturedDetail}
                />
              ))}
            </div>
          </motion.section>
        ) : null}
      </div>
    </div>
  );
}
