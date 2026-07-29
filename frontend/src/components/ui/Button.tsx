import type { ButtonHTMLAttributes, ReactNode } from "react";

import { LoaderCircle } from "lucide-react";

import { cn } from "@/lib/cn";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "default" | "icon";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

const variants: Record<ButtonVariant, string> = {
  primary:
    "border border-forest-600 bg-forest-600 text-white hover:border-forest-700 hover:bg-forest-700 focus-visible:ring-forest-600",
  secondary:
    "border border-slate-200 bg-white text-slate-800 hover:border-slate-300 hover:bg-slate-50 focus-visible:ring-forest-600",
  ghost:
    "border border-transparent text-slate-500 hover:bg-forest-600/6 hover:text-forest-600 focus-visible:ring-forest-600",
  danger: "border border-red-700 bg-red-700 text-white hover:bg-red-800 focus-visible:ring-red-600",
};

const sizes: Record<ButtonSize, string> = {
  default: "min-h-[42px] px-[18px] py-2.5 max-[480px]:px-4 max-[480px]:text-[13px]",
  icon: "size-[42px] shrink-0 p-0",
};

export function Button({
  children,
  className,
  variant = "primary",
  size = "default",
  loading = false,
  disabled,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={cn(
        "inline-flex cursor-pointer items-center justify-center gap-2 rounded-[10px] text-sm font-semibold leading-none transition-[background,border-color,box-shadow,transform] duration-150 focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {loading && <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />}
      {children}
    </button>
  );
}
