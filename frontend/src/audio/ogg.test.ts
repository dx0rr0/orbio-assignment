import { describe, expect, it } from 'vitest'

import { encodeOggOpus, oggCrc, opusHead, type OpusPacket } from './ogg'

interface Page {
  flags: number
  granule: number
  sequence: number
  packets: Uint8Array[]
  crcMatches: boolean
}

/** Reads the pages back, joining each packet's lacing segments. */
function readPages(file: Uint8Array): Page[] {
  const pages: Page[] = []
  for (let offset = 0; offset < file.length; ) {
    const view = new DataView(file.buffer, file.byteOffset + offset)
    expect(String.fromCharCode(...file.subarray(offset, offset + 4))).toBe('OggS')
    const lacing = Array.from(file.subarray(offset + 27, offset + 27 + file[offset + 26]))
    const size = 27 + lacing.length + lacing.reduce((total, value) => total + value, 0)
    const bytes = file.slice(offset, offset + size)
    const crc = new DataView(bytes.buffer).getUint32(22, true)
    new DataView(bytes.buffer).setUint32(22, 0, true)
    const packets: Uint8Array[] = []
    let start = 27 + lacing.length
    let length = 0
    for (const value of lacing) {
      length += value
      if (value < 255) {
        packets.push(bytes.slice(start, start + length))
        start += length
        length = 0
      }
    }
    pages.push({
      flags: view.getUint8(5),
      granule: Number(view.getBigUint64(6, true)),
      sequence: view.getUint32(18, true),
      packets,
      crcMatches: crc === oggCrc(bytes),
    })
    offset += size
  }
  return pages
}

const packet = (length: number, fill = 1): OpusPacket => ({
  data: new Uint8Array(length).fill(fill),
  samples: 960,
})

describe('oggCrc', () => {
  it('matches the reference value of the Ogg CRC-32', () => {
    expect(oggCrc(new TextEncoder().encode('123456789'))).toBe(0x89a1897f)
  })
})

describe('encodeOggOpus', () => {
  const head = opusHead(48_000, 312)

  it('puts the headers on pages of their own, then the audio', () => {
    const pages = readPages(encodeOggOpus(head, [packet(60), packet(70), packet(80)]))

    expect(pages.map((page) => page.sequence)).toEqual([0, 1, 2, 3])
    expect(pages.map((page) => page.flags)).toEqual([0x02, 0, 0, 0x04])
    expect(pages[0].packets).toEqual([head])
    expect(String.fromCharCode(...pages[1].packets[0].subarray(0, 8))).toBe('OpusTags')
    expect(pages.slice(2).map((page) => page.packets.map((p) => p.length))).toEqual([
      [60, 70],
      [80],
    ])
    expect(pages.every((page) => page.crcMatches)).toBe(true)
  })

  // MAI Transcribe rejects (HTTP 400) an Ogg file whose audio is all on one page.
  it('puts the last packet on a page of its own, so the audio spans at least two', () => {
    const pages = readPages(encodeOggOpus(head, [packet(60), packet(60)])).slice(2)

    expect(pages.map((page) => page.packets.length)).toEqual([1, 1])
    expect(pages.map((page) => page.flags)).toEqual([0, 0x04])
  })

  it('counts granule positions in 48 kHz samples from the pre-skip', () => {
    const pages = readPages(encodeOggOpus(head, [packet(60), packet(60), packet(60)]))

    expect(pages.map((page) => page.granule)).toEqual([0, 0, 312 + 2 * 960, 312 + 3 * 960])
  })

  it('laces packets of 255 bytes or more over several segments', () => {
    const packets = [packet(255, 2), packet(600, 3), packet(10, 4)]

    const audio = readPages(encodeOggOpus(head, packets)).slice(2)

    expect(audio.flatMap((page) => page.packets)).toEqual(packets.map((p) => p.data))
  })

  it('starts a new page every second of audio', () => {
    const packets = Array.from({ length: 120 }, (_, i) => packet(50, i % 250))

    const pages = readPages(encodeOggOpus(head, packets)).slice(2)

    expect(pages.map((page) => page.packets.length)).toEqual([50, 50, 19, 1])
    expect(pages.map((page) => page.flags)).toEqual([0, 0, 0, 0x04])
    expect(pages.map((page) => page.granule)).toEqual(
      [50, 100, 119, 120].map((n) => 312 + n * 960),
    )
    expect(pages.flatMap((page) => page.packets)).toEqual(packets.map((p) => p.data))
  })

  it('starts a new page when the lacing values run out', () => {
    const packets = Array.from({ length: 10 }, () => packet(255 * 30))

    const pages = readPages(encodeOggOpus(head, packets)).slice(2)

    // 31 lacing values each: 8 packets fill 248 of the 255.
    expect(pages.map((page) => page.packets.length)).toEqual([8, 1, 1])
  })
})

describe('opusHead', () => {
  it('writes a mono identification header', () => {
    const head = opusHead(16_000, 104)
    const view = new DataView(head.buffer)

    expect(String.fromCharCode(...head.subarray(0, 8))).toBe('OpusHead')
    expect([head[8], head[9], view.getUint16(10, true), view.getUint32(12, true)]).toEqual([
      1, 1, 104, 16_000,
    ])
    expect(head).toHaveLength(19)
  })
})
