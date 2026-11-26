import { cn } from "@/utils/cn";
import { initials } from "@/utils/initials";

const SIZES = { sm: "h-7 w-7 text-2xs", md: "h-8 w-8 text-xs", lg: "h-10 w-10 text-sm" };

export interface AvatarProps {
  name: string;
  size?: keyof typeof SIZES;
  className?: string;
}

/** Initials avatar. Decorative: the person's name is always shown or labelled next to it. */
export function Avatar({ name, size = "md", className }: AvatarProps) {
  return (
    <span
      aria-hidden="true"
      className={cn("grid shrink-0 place-items-center rounded-full bg-accent-subtle font-semibold text-accent-text select-none", SIZES[size], className)}
    >
      {initials(name)}
    </span>
  );
}
