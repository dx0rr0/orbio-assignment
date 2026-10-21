/** A minimal Ogg Opus muxer (RFC 3533, RFC 7845) for one mono recording. */

export interface OpusPacket {
  data: Uint8Array
  /** Its duration in 48 kHz samples, the clock of Ogg Opus granule positions. */
  samples: number
}

// An Ogg page holds at most 255 lacing values.
const MAX_SEGMENTS = 255
const SERIAL = 1

const CRC_TABLE = Uint32Array.from({ length: 256 }, (_, n) => {
  let crc = n << 24
  for (let bit = 0; bit < 8; bit++) crc = crc & 0x80000000 ? (crc << 1) ^ 0x04c11db7 : crc << 1
  return crc >>> 0
})

/** Ogg's CRC-32: polynomial 0x04c11db7, no reflection, initial value and final XOR zero. */
export function oggCrc(bytes: Uint8Array): number {
  let crc = 0
  for (const byte of bytes) crc = ((crc << 8) ^ CRC_TABLE[((crc >>> 24) ^ byte) & 0xff]) >>> 0
  return crc
}

/** The identification header, for an encoder that gives none. */
export function opusHead(sampleRate: number, preSkip: number): Uint8Array {
  const head = new Uint8Array(19)
  const view = new DataView(head.buffer)
  head.set(ascii('OpusHead'))
  head[8] = 1 // version
  head[9] = 1 // mono
  view.setUint16(10, preSkip, true)
  view.setUint32(12, sampleRate, true) // the input rate, informational
  // Output gain 0 and channel mapping family 0: the rest stays zero.
  return head
}

/**
 * The Ogg Opus file of `packets`: the `head` (OpusHead) and an empty OpusTags on pages of
 * their own, then the audio packets.
 */
export function encodeOggOpus(head: Uint8Array, packets: OpusPacket[]): Uint8Array {
  const preSkip = new DataView(head.buffer, head.byteOffset).getUint16(10, true)
  const pages: Uint8Array[] = [page([head], 0, 0, 0x02), page([opusTags()], 0, 1, 0)]
  let granule = preSkip
  let batch: Uint8Array[] = []
  let segments = 0
  const flush = (last: boolean) => {
    pages.push(page(batch, granule, pages.length, last ? 0x04 : 0))
    batch = []
    segments = 0
  }
  packets.forEach((packet, i) => {
    const needed = Math.floor(packet.data.length / 255) + 1
    if (segments + needed > MAX_SEGMENTS) flush(false)
    batch.push(packet.data)
    segments += needed
    granule += packet.samples
    if (i === packets.length - 1) flush(true)
  })
  if (packets.length === 0) flush(true)
  return concat(pages)
}

function opusTags(): Uint8Array {
  const vendor = ascii('orbio')
  const tags = new Uint8Array(8 + 4 + vendor.length + 4)
  const view = new DataView(tags.buffer)
  tags.set(ascii('OpusTags'))
  view.setUint32(8, vendor.length, true)
  tags.set(vendor, 12)
  // No user comments: the count after the vendor stays zero.
  return tags
}

/** One page with whole `packets`; the granule position counts samples up to its end. */
function page(packets: Uint8Array[], granule: number, sequence: number, flags: number): Uint8Array {
  const lacing: number[] = []
  for (const packet of packets) {
    for (let left = packet.length; ; left -= 255) {
      lacing.push(Math.min(left, 255))
      if (left < 255) break
    }
  }
  const header = new Uint8Array(27 + lacing.length)
  const view = new DataView(header.buffer)
  header.set(ascii('OggS'))
  header[5] = flags // 0x02 first page, 0x04 last page
  view.setBigUint64(6, BigInt(granule), true)
  view.setUint32(14, SERIAL, true)
  view.setUint32(18, sequence, true)
  header[26] = lacing.length
  header.set(lacing, 27)
  const bytes = concat([header, ...packets])
  // The checksum is computed with its own field zeroed, as it is here.
  new DataView(bytes.buffer).setUint32(22, oggCrc(bytes), true)
  return bytes
}

function ascii(text: string): Uint8Array {
  return Uint8Array.from(text, (c) => c.charCodeAt(0))
}

function concat(parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((total, part) => total + part.length, 0))
  let offset = 0
  for (const part of parts) {
    out.set(part, offset)
    offset += part.length
  }
  return out
}
