import type { ModelOption, SessionConfig } from '@/types/app';
interface WelcomePageProps {
    value: string;
    disabled?: boolean;
    models: ModelOption[];
    config: SessionConfig;
    onChange: (value: string) => void;
    onSubmit: () => void;
    onUpload: (file: File) => void;
    onConfigChange: (config: SessionConfig) => void;
}
export default function WelcomePage({ value, disabled, models, config, onChange, onSubmit, onUpload, onConfigChange, }: WelcomePageProps): import("react/jsx-runtime").JSX.Element;
export {};
