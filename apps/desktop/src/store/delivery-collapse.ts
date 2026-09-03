import { type Codec, persistentAtom } from '@/lib/persisted'

export type DeliveryCollapse = 'collapsed' | 'expanded'

const STORAGE_KEY = 'hermes.desktop.deliveryCollapse'

// Controller deliveries (cron drops, mission callbacks) collapse by default so
// a control conversation reads as a log; a delivery that needs the owner
// (ChatMessage.delivery.needsOwner) stays expanded regardless.
const collapseCodec: Codec<DeliveryCollapse> = {
  decode: raw => (raw === 'expanded' ? raw : 'collapsed'),
  encode: value => value
}

export const $deliveryCollapse = persistentAtom<DeliveryCollapse>(STORAGE_KEY, 'collapsed', collapseCodec)

export function setDeliveryCollapse(value: DeliveryCollapse) {
  $deliveryCollapse.set(value)
}
