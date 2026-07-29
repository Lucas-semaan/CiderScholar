import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

import { cn } from "@/lib/cn";

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("grid gap-1.5 text-sm font-medium text-slate-700", className)}>
      <span>{label}</span>
      {children}
      {hint && <span className="text-xs font-normal text-slate-500">{hint}</span>}
    </label>
  );
}

const control =
  "min-h-[42px] w-full rounded-[10px] border border-slate-200 bg-white px-3.5 text-sm text-slate-800 shadow-soft transition placeholder:text-slate-400 hover:border-slate-300 focus:border-forest-600 focus:ring-3 focus:ring-forest-600/10";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(control, className)} {...props} />;
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(control, "min-h-28 resize-y py-3", className)} {...props} />;
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(control, "cursor-pointer", className)} {...props} />;
}
