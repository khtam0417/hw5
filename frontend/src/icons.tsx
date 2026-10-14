// Small line icons, drawn for this dashboard. They inherit the text color and are hidden from screen readers:
// every icon sits next to a text label that says the same thing.
import type { ReactNode, SVGProps } from 'react'

export type IconProps = SVGProps<SVGSVGElement>

function Icon({ children, ...props }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  )
}

// The five agents
export const CrownIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M3.5 8.5 8 12l4-6.5 4 6.5 4.5-3.5-1.8 10H5.3z" />
    <path d="M6 21h12" />
  </Icon>
)
export const BoxIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M21 8 12 3 3 8v8l9 5 9-5z" />
    <path d="m3 8 9 5 9-5" />
    <path d="M12 13v8" />
    <path d="m7.5 5.5 9 5" />
  </Icon>
)
export const CoinsIcon = (props: IconProps) => (
  <Icon {...props}>
    <ellipse cx="12" cy="6" rx="7" ry="2.8" />
    <path d="M5 6v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6" />
    <path d="M5 12v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8v-6" />
  </Icon>
)
export const BuildingIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M4 21V5.5A1.5 1.5 0 0 1 5.5 4h8A1.5 1.5 0 0 1 15 5.5V21" />
    <path d="M15 10h3.5a1.5 1.5 0 0 1 1.5 1.5V21" />
    <path d="M2.5 21h19" />
    <path d="M8 8h3M8 12h3M8 16h3" />
  </Icon>
)
export const ChatIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M20.5 11.5a8 8 0 0 1-11.7 7.1L4 19.8l1.3-4.4a8 8 0 1 1 15.2-3.9z" />
    <path d="M8.5 11.5h.01M12.5 11.5h.01M16.5 11.5h.01" />
  </Icon>
)

// Statuses and actions
export const CheckIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Icon>
)
export const ClockIcon = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Icon>
)
export const TruckIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M2.5 6.5h11v9h-11z" />
    <path d="M13.5 9.5h4l3 3.5v2.5h-7" />
    <circle cx="6.5" cy="17.5" r="1.8" />
    <circle cx="17" cy="17.5" r="1.8" />
  </Icon>
)
export const UserIcon = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="12" cy="8" r="3.5" />
    <path d="M5 20.5c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" />
  </Icon>
)
export const AlertIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M12 3.5 21.5 20h-19z" />
    <path d="M12 10v4.5M12 17.4v.1" />
  </Icon>
)
export const XIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />
  </Icon>
)
export const PlayIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M8 5.5v13l10.5-6.5z" />
  </Icon>
)
export const BoltIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M13 2.5 4.5 14H11l-1 7.5L19.5 10H13z" />
  </Icon>
)
export const ArrowRightIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M4.5 12h15M13.5 6l6 6-6 6" />
  </Icon>
)
export const ReturnIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M9.5 15 4.5 10l5-5" />
    <path d="M4.5 10h9a5.5 5.5 0 0 1 0 11H11" />
  </Icon>
)
export const FlagIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M5 21V4" />
    <path d="M5 4.5h11.5l-2 4 2 4H5" />
  </Icon>
)
export const DraftIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M7 3h7l5 5v13H7z" />
    <path d="M14 3v5h5M10 13h6M10 17h4" />
  </Icon>
)
export const DollarIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M12 3v18" />
    <path d="M16.5 7.5c0-1.9-2-3-4.5-3s-4.5 1.1-4.5 3 2 2.7 4.5 3.2 4.5 1.4 4.5 3.3-2 3-4.5 3-4.5-1.1-4.5-3" />
  </Icon>
)
export const CircleIcon = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="12" cy="12" r="7" />
  </Icon>
)
export const RefreshIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M20 11a8 8 0 0 0-14.7-4.3L3.5 9" />
    <path d="M3.5 4.5V9H8" />
    <path d="M4 13a8 8 0 0 0 14.7 4.3l1.8-2.3" />
    <path d="M20.5 19.5V15H16" />
  </Icon>
)
export const DatabaseIcon = (props: IconProps) => (
  <Icon {...props}>
    <ellipse cx="12" cy="5.5" rx="7.5" ry="2.5" />
    <path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13" />
    <path d="M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5" />
  </Icon>
)
export const CalendarIcon = (props: IconProps) => (
  <Icon {...props}>
    <rect x="3.5" y="5" width="17" height="15.5" rx="2" />
    <path d="M3.5 9.5h17M8 3v4M16 3v4" />
  </Icon>
)
export const FlaskIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M9 3h6M10 3v6l-5.4 9.4A1.7 1.7 0 0 0 6.1 21h11.8a1.7 1.7 0 0 0 1.5-2.6L14 9V3" />
    <path d="M7.5 15h9" />
  </Icon>
)
export const SparkIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" />
  </Icon>
)
export const SpinnerIcon = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="12" cy="12" r="8.5" opacity="0.25" />
    <path d="M20.5 12A8.5 8.5 0 0 0 12 3.5" />
  </Icon>
)
