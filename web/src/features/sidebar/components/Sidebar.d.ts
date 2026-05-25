import type { SessionSummary } from "@/types/app";
interface SidebarProps {
    sessions: SessionSummary[];
    activeSessionId: string;
    onNewSession: () => void;
    onSelectSession: (sessionId: string) => void;
    onDeleteSession: (sessionId: string) => void;
}
export declare function Sidebar({ sessions, activeSessionId, onNewSession, onSelectSession, onDeleteSession, }: SidebarProps): import("react/jsx-runtime").JSX.Element;
export {};
