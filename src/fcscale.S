.syntax unified
.arm
.text
/* False Color / EL Zone as a toggle on the button you already mapped, with the
 * camera's own EL Zone scale drawn across the bottom of live view.
 *
 * Self-contained: no menu, no key hook, no panel, nothing bound to RIGHT or UP.
 * The only button involved is the one the camera's own Custom Button
 * Functions menu already points at False Color.
 *
 *   short release: off -> colours, with the scale as it was last left
 *                  (off after boot); colours/both -> off, scale remembered
 *   half-second hold: off -> both; colours <-> both (styles with a scale);
 *                  the hold's result is what the next ON restores (Fix 14)
 *
 * The camera calls CameraIF vtable +0xCC on press and +0xD0 on release; both
 * are replaced outright (see docs/research/false-colour-el-zone.md).
 *
 * The scale is the firmware's own. Its band table (0xC0D1B74C: fifteen
 * x-ranges, each with a palette index), its 256-entry palette (0xC0D1B34C)
 * and its label glyphs are read out of the verified MAIN image by the card
 * builder, which writes them into fcscale_native.inc.S. Nothing about the
 * colours is invented here.
 *
 * The drawing surface is the LCD's main OSD layer: 16-bit pixels, alpha in
 * the top nibble, descriptor format 1, resolved through the display manager
 * the way the firmware's own FalseColorBarDrawer resolves its layer
 * (0xC057DA78). Colours are direct, so no palette is involved; the builder
 * converts the firmware's A,Y,U,V scale entries with the firmware's own
 * matrix (0xC03E4C38) and packs them 4:4:4. Routines take the pixel size
 * from STATE +0x8C, so the same code draws an indexed layer with PAINT_*
 * changed.
 *
 * Painting is done inside the firmware's own display submit: 0xC02E8A08 is
 * entered through fc_submit, which paints the scale into the exact buffer
 * being sent to the panel. The layer rotates three buffers; each one is
 * tracked with a drawn flag so a buffer carrying the scale is cleared once
 * when the scale goes away. A press paints the layer's front buffer directly,
 * the one the panel is scanning, so the change is immediate rather than at
 * the UI's next frame (see fc_present for why not a submit).
 *
 * State block at STATE:
 *   +0x00 0 off, 1 on, 2 on with the scale   +0x3C buffer being painted
 *   +0x04 presses                            +0x40 current colour
 *   +0x08 releases swallowed                 +0x60..+0x68 drawn flag per buffer
 *   +0x0C the scale flag                     +0x6C paint lock
 *   +0x20 width in pixels, while painting    +0x74 submits seen
 *   +0x24 height, while painting             +0x78 frames painted
 *   +0x28..+0x30 the layer's three buffers   +0x7C next slot to evict
 *                                            +0x8C bytes per pixel, while painting
 *   +0xD0 False Color Style last read: 0 False Color, 1 EL Zone
 *   +0xD4 the same, latched for the paint in progress
 *   +0xD8 ON requests re-sent for the start in flight (Fix 13)
 *
 * Fix 13: EL Zone bands keep their native colours at every ISO; only the
 * red labels (+4..+6 at ISO 100/200, +6 at 400/3200) still follow ISO. An
 * ON request the camera ignores is re-sent (fc_readiness).
 *
 * Fix 12: style 0 draws the firmware's False Color scale (0xC0D1B7C4,
 * FalseColorScale labels) in the same place, with the same gestures; EL Zone
 * is unchanged. Any other style value still gets colours only.
 */
.equ MEMSET,     0xC0015058
.equ POST,       0xC03A0798
.equ DRAW_MGR,   0xC0698D80         /* display manager; [r0+4]+0xC resolves a layer */
/* Which OSD layer the scale goes on. Main is 16-bit direct colour (format 1,
 * alpha in the top nibble, 0xC052A528); sub is one byte per pixel through a
 * palette (format 3). Two palette installs on the sub layer did not take on
 * the camera (2026-09-20: setPalette at the press, then the descriptor's
 * palette pointer at each submit), so the scale is drawn on main. */
.equ PAINT_LAYER, 0                 /* manager selector and submit sub flag: 0 main, 1 sub */
.equ PAINT_FMT,   1                 /* the descriptor format that layer must report */
.equ PAINT_BPP,   2                 /* bytes per pixel there */
.equ UI_STATE,   0xC3033A44         /* 2 live view, 4 playback, 5 camera menu */
.equ UI_PLAYBACK, 4
.equ UI_MENU,     5
.equ CACHE_FN,   0xC000E91C
/* Where the firmware draws its own scale, in sub-layer pixels: the bar is
 * memset row 464 and copied down to row 582 (0xC057DC94, 0xC057DD1C); the
 * labels sit in the 25-row band above it. */
.equ SCALE_W,    1024
.equ BAR_Y,      590
.equ BAR_H,      39
.equ LABEL_Y,    559
.equ CLEAR_Y,    (LABEL_Y - 3)
.equ CLEAR_H,    (BAR_Y + BAR_H - CLEAR_Y)
.equ C_CLEAR,    0                  /* transparent: index 0, or alpha nibble 0 */

#include "fcscale_native.inc.S"

.macro LDA reg, val
    movw \reg, #:lower16:\val
    movt \reg, #:upper16:\val
.endm

/* Forward label addresses stay position independent. */
.macro PTR reg, label
    ldr \reg, 991f
990: add \reg, pc, \reg
    b 992f
991: .word \label - (990b + 8)
992:
.endm

/* ---------------------------------------------------------------- the hooks */

/* Gesture experiment: +0x10 held, +0x14 start tick, +0x18 CameraIF.
 * Half-second hold on an independent task. TICK_US follows fpSup; hardware timing
 * and the style getter must still be validated on this body. */
.equ TICK_US, 0xC002B6E0
.equ SETTINGS_GET, 0xC0057AE8
.equ STYLE_GET, 0xC005DBD8
.global fc_press
fc_press:
    push {r4, r5, r6, lr}
    mov r4, r0
    bl fc_start                 @ lazy restart after a new CameraIF boot epoch
    LDA r5, STATE
    ldr r1, [r5, #0x10]
    cmp r1, #0
    bne 9f
    LDA ip, SETTINGS_GET
    blx ip
    LDA ip, STYLE_GET
    blx ip
    str r0, [r5, #0x84]
    LDA ip, TICK_US
    blx ip
    str r0, [r5, #0x14]
    str r4, [r5, #0x18]
    dmb
    mov r1, #1
    str r1, [r5, #0x10]
9:  mov r0, #1
    pop {r4, r5, r6, pc}

/* The independent task checks the deadline every 20 ms and paints the front.
 * CAS consumes a hold once; release atomically cancels a pending hold. */
.global fc_poll
fc_poll:
    push {r4, r5, r6, lr}
    LDA r5, STATE
    ldr r1, [r5, #0x10]
    cmp r1, #1
    bne 9f
    LDA ip, TICK_US
    blx ip
    ldr r1, [r5, #0x14]
    sub r0, r0, r1
    LDA r1, 500000
    cmp r0, r1
    blo 9f
    add r4, r5, #0x10
1:  ldrex r0, [r4]
    cmp r0, #1
    bne 8f
    mov r1, #2
    strex r2, r1, [r4]
    cmp r2, #0
    bne 1b
    dmb
    mov r0, #1
    mov r1, #1
    bl fc_action
    b 9f
8:  clrex
9:  pop {r4, r5, r6, pc}

.global fc_release
fc_release:
    push {r4, r5, r6, lr}
    LDA r5, STATE
    add r2, r5, #0x10
    mov r1, #0
    swp r4, r1, [r2]
    dmb
    cmp r4, #1
    bne 9f                       @ already handled by timer, or stray release
    LDA ip, TICK_US
    blx ip
    ldr r1, [r5, #0x14]
    sub r0, r0, r1
    LDA r1, 500000
    cmp r0, r1
    movhs r0, #1                @ fallback if there were no display submissions
    movlo r0, #0
    mov r1, #1
    bl fc_action
9:  mov r0, #1
    pop {r4, r5, r6, pc}

/* r0: long? r1: key context may repaint front? First-version rendering. */
fc_action:
    push {r4, r5, r6, r7, r8, lr}
    mov r6, r1
    LDA r5, STATE
    ldr r4, [r5, #0x18]
    mov r8, r0
    bl fc_visibility
    LDA r0, UI_STATE
    ldr r0, [r0]
    cmp r0, #2
    cmpne r0, #9
    bne 9f                      @ do not enable effects from another screen
    ldr r7, [r5]
    cmp r1, #0
    bne 11f
    ldr r0, [r5, #0xA4]          @ retain an in-flight start for rapid double taps
    cmp r0, #0
    bne 11f
    mov r7, #0                  @ autofocus/native stop: next press is ON
    str r7, [r5, #0x9C]
/* Fix 14: +0xB4 is the legend as the user last left it. Only a hold that
 * changes the legend writes it; turning off keeps it, turning on restores it.
 * Zero after boot, so the first ON is colours without the legend. */
11: mov r0, r8
    cmp r0, #0
    bne 2f
    cmp r7, #0
    movne r8, #0               @ on -> off; the legend preference is kept
    bne 4f
    ldr r0, [r5, #0xB4]        @ off -> on with the legend as last left
    cmp r0, #0
    moveq r8, #1
    movne r8, #2
    b 4f
2:  ldr r0, [r5, #0x84]
    cmp r0, #1
    cmpne r0, #0
    bne 3f
    cmp r7, #2
    moveq r8, #1
    movne r8, #2
    cmp r8, #2                 @ a hold sets the legend: remember it
    moveq r0, #1
    movne r0, #0
    str r0, [r5, #0xB4]
    b 4f
3:  mov r8, #1
4:  cmp r8, #2
    bne 40f
    ldr r0, [r5, #0x84]
    cmp r0, #1
    cmpne r0, #0
    movne r8, #1               @ styles without a native scale get colours only
40: str r8, [r5]
    mov r0, #0
    str r0, [r5, #0xB8]         @ explicit gesture supersedes suspension
    str r0, [r5, #0xC8]
    cmp r8, #0
    moveq r0, #0
    streq r0, [r5, #0x9C]
    streq r0, [r5, #0xA4]
    cmp r8, #2
    moveq r1, #1
    movne r1, #0
    str r1, [r5, #0xC]
    mov r0, #0
    str r0, [r5, #0x90]
    cmp r8, #2
    bne 41f
    LDA ip, TICK_US
    blx ip
    str r0, [r5, #0x94]
41: cmp r8, #0
    beq 6f
    cmp r7, #0
    bne 5f
    LDA ip, TICK_US
    blx ip
    str r0, [r5, #0xA0]
    mov r0, #1
    str r0, [r5, #0xA4]
    mov r0, #0
    str r0, [r5, #0xAC]
    str r0, [r5, #0xB0]
    str r0, [r5, #0xD8]         @ a fresh request: retries start again
    mov r0, r4
    mov r1, #0x21
    bl fc_post
5:  cmp r6, #0
    beq 9f
    bl fc_present
    b 9f
6:  bl fc_present            @ restore first-version immediate legend clearing
    mov r0, r4
    mov r1, #0x22
    bl fc_post
9:  mov r0, #1
    pop {r4, r5, r6, r7, r8, pc}

/* r0 = the CameraIF object, r1 = event id. The stock method body, reproduced,
 * so nothing calls back into a patched method. */
fc_post:
    push    {r4, r5, lr}
    mov     r4, r0
    mov     r5, r1
    sub     sp, sp, #0xBC
    mov     r0, sp
    mov     r1, #0
    mov     r2, #0xBC
    LDA     r3, MEMSET
    blx     r3
    str     r5, [sp]
    mov     r1, #1
    strb    r1, [sp, #4]
    ldr     r0, [r4, #4]
    mov     r1, sp
    LDA     r3, POST
    blx     r3
    add     sp, sp, #0xBC
    pop     {r4, r5, pc}

/* ------------------------------------------------------------- the surfaces */

/* The sub layer's drawable, the way the firmware's own bar drawer gets it:
 * display manager, then its interface's slot +0xC with the layer selector.
 * Returns 0 if the manager is not up. */
layer_drawable:
    push    {r4, lr}
    LDA     ip, DRAW_MGR
    blx     ip
    cmp     r0, #0
    beq     9f
    ldr     r1, [r0, #4]
    ldr     r3, [r1, #0xC]
    mov     r1, #PAINT_LAYER
    blx     r3
9:  pop     {r4, pc}


/* ------------------------------------------------------------- the painting */

/* Placed over the first instruction of 0xC02E8A08, the display submit:
 * submit(controller, descriptor, sub). Every buffer the UI sends to the panel
 * passes through here first, so the scale is painted into exactly the buffer
 * about to be shown, once per frame, with no thread and no cached buffer
 * addresses. The displaced push {r4, r5, r6, r7, fp, lr} is replayed and
 * control returns to the instruction after it. */
.global fc_submit
fc_submit:
    push    {r0, r1, r2, r3, ip, lr}
    bl      fc_on_submit
    pop     {r0, r1, r2, r3, ip, lr}
    push    {r4, r5, r6, r7, fp, lr}
    LDA     ip, SUBMIT_RESUME
    bx      ip

/* fc_on_submit(controller, descriptor, sub): paint the frame on its way out. */
fc_on_submit:
    push    {r4, lr}
    LDA     r4, STATE
    ldr     r3, [r4, #0x74]
    add     r3, r3, #1
    str     r3, [r4, #0x74]
    mov     r0, r1
    bl      fc_paint
    pop     {r4, pc}

/* fc_paint(descriptor). Acts only on a descriptor of PAINT_FMT, of the
 * scale's width and at least its height; anything else (the other layer,
 * another monitor) is left alone.
 *
 * Each of the layer's buffers is tracked with a drawn flag: the scale is
 * painted whenever it is wanted, and a buffer that carries it is cleared once
 * when it is not, so nothing of ours stays on a buffer the UI recycles. A
 * word at +0x6C serialises the hook against a press. */
fc_paint:
    push    {r4, r5, r6, r7, r8, lr}
    LDA     r8, STATE
    cmp     r0, #0
    beq     9f
    ldr     r3, [r0]
    cmp     r3, #PAINT_FMT
    bne     9f
    mov     r2, #PAINT_BPP
    str     r2, [r8, #0x8C]
    ldr     r4, [r0, #4]            @ base
    cmp     r4, #0
    beq     9f
    ldr     r3, [r0, #8]
    cmp     r3, #0
    beq     9f
    ldr     r5, [r3]                @ width, in pixels
    ldr     r6, [r3, #4]            @ height
    movw    ip, #SCALE_W
    cmp     r5, ip
    bne     9f
    movw    ip, #(CLEAR_Y + CLEAR_H)
    cmp     r6, ip
    blo     9f

    LDA     r0, UI_STATE
    ldr     r0, [r0]
    cmp     r0, #UI_PLAYBACK
    cmpne   r0, #UI_MENU
    beq     fc_yield_surface
    bl      fc_visibility
    ldr     r1, [r8, #0xB0]
    and     r7, r0, r1
    ldr     r0, [r8]
    cmp     r0, #2
    movne   r7, #0

    mov     r0, #0                  @ find this buffer's slot, or take one
1:  add     r1, r8, r0, lsl #2
    ldr     r2, [r1, #0x28]
    cmp     r2, r4
    beq     2f
    cmp     r2, #0
    beq     3f
    add     r0, r0, #1
    cmp     r0, #3
    blo     1b
    ldr     r0, [r8, #0x7C]         @ all three taken by others: evict in turn
    add     r2, r0, #1
    cmp     r2, #3
    moveq   r2, #0
    str     r2, [r8, #0x7C]
    add     r1, r8, r0, lsl #2
3:  str     r4, [r1, #0x28]
    mov     r2, #0
    str     r2, [r1, #0x60]
2:  ldr     r2, [r1, #0x60]         @ drawn on this buffer
    orrs    r2, r2, r7
    beq     9f                      @ nothing there and nothing wanted

    add     r3, r8, #0x6C           @ take the paint lock, or leave this frame.
    mov     r1, #1                  @ swp, which is what this firmware uses
    swp     r2, r1, [r3]            @ (106 sites, no ldrex/strex anywhere)
    cmp     r2, #0
    bne     9f
    dmb
    add     r1, r8, r0, lsl #2      @ the slot again; r1 was the swap value
    str     r4, [r8, #0x3C]
    str     r5, [r8, #0x20]
    str     r6, [r8, #0x24]
    mov     r6, r1                  @ the slot, across the calls below
    ldr     r0, [r8, #0xBC]
    str     r0, [r8, #0xC4]         @ immutable variant for this paint
    ldr     r0, [r8, #0xD0]
    str     r0, [r8, #0xD4]         @ and which legend, likewise
    mov     r0, r4
    mov     r1, r7
    bl      draw_scale
    str     r7, [r6, #0x60]
    ldr     r0, [r8, #0x78]
    add     r0, r0, #1
    str     r0, [r8, #0x78]
    movw    r1, #CLEAR_Y            @ the panel reads memory: clean our rows
    mul     r1, r5, r1              @ out of the data cache, and only ours --
    ldr     r2, [r8, #0x8C]         @ the firmware's 0xC000E91C cleans every
    mla     r0, r1, r2, r4          @ set and way of both levels with
    mov     r1, #CLEAR_H            @ interrupts off, too much for every frame
    mul     r1, r5, r1
    mul     r1, r2, r1
    bl      clean_range
6:  mov     r0, #0
    str     r0, [r8, #0x6C]
9:  pop     {r4, r5, r6, r7, r8, pc}

/* Menu/playback owns its rendered surface. Never blank an old legend
 * rectangle over a newly drawn menu. Drop only our ownership metadata.
 */
fc_yield_surface:
    mov r0, #0
1:  add r1, r8, r0, lsl #2
    ldr r2, [r1, #0x28]
    cmp r2, r4
    moveq r2, #0
    streq r2, [r1, #0x60]
    add r0, r0, #1
    cmp r0, #3
    blo 1b
    pop {r4, r5, r6, r7, r8, pc}

/* clean_range(address, length): data cache clean by address to the point of
 * coherency, 32-byte steps (a 64-byte line is simply cleaned twice). */
clean_range:
    add     r1, r0, r1
    bic     r0, r0, #31
1:  mcr     p15, #0, r0, c7, c10, #1
    add     r0, r0, #32
    cmp     r0, r1
    blo     1b
    dsb
    bx      lr

/* fc_present: paint the layer's front buffer, the one the panel is scanning
 * now, so a press shows or clears the scale immediately instead of at the
 * UI's next frame. No rotate and no submit: a rotated-and-submitted frame
 * from the press was not shown once the UI had gone quiet after the mode
 * change (camera, 2026-09-20; a second press during the UI's own redraw
 * was), while direct writes into the layer's buffers were seen on the panel
 * from the first build. The hook keeps repainting whatever the UI submits
 * afterwards. */
fc_present:
    push    {r4, lr}
    bl      layer_drawable
    cmp     r0, #0
    beq     9f
    mov     r4, r0
    ldr     r3, [r4]
    ldr     r3, [r3, #0x0C]         @ front descriptor
    mov     r0, r4
    blx     r3
    cmp     r0, #0
    beq     9f
    bl      fc_paint
9:  pop     {r4, pc}

/* draw_scale(base, wanted): clear the scale's rows, then draw it if wanted.
 * Refuses a surface the firmware's own geometry does not fit on. */
draw_scale:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    str     r0, [r10, #0x3C]
    mov     r9, r1
    ldr     r7, [r10, #0x20]        @ stride
    movw    r0, #SCALE_W
    cmp     r7, r0
    blo     9f
    ldr     r8, [r10, #0x24]        @ height
    movw    r0, #(CLEAR_Y + CLEAR_H)
    cmp     r8, r0
    blo     9f

    mov     r0, #C_CLEAR
    cmp     r9, #0
    movwne  r0, #0x8000         @ 8/15 alpha black backdrop: 8px sides, no bottom margin
    str     r0, [r10, #0x40]
    mov     r0, #43
    movw    r1, #CLEAR_Y
    movw    r2, #937
    mov     r3, #CLEAR_H
    bl      fill_rect
    cmp     r9, #0
    beq     9f
    ldr     r0, [r10, #0xD4]        @ 0: the False Color scale instead
    cmp     r0, #0
    beq     30f

    PTR     r4, bands              @ {u16 x0, u16 x1, u8 index, u8 0, u16 colour16}
    mov     r5, #BAND_N
1:  ldrh    r0, [r4]
    ldrh    r1, [r4, #2]
    ldr     r2, [r10, #0x8C]
    cmp     r2, #2
    ldrheq  r2, [r4, #6]            @ direct colour on a 16-bit layer
    ldrbne  r2, [r4, #4]            @ the palette index on an indexed one
    str     r2, [r10, #0x40]        @ Fix 13: native colours at every ISO
    sub     r2, r1, r0
    add     r2, r2, #1              @ x1 is inclusive
    movw    r1, #BAR_Y
    mov     r3, #BAR_H
    bl      fill_rect
    add     r4, r4, #8
    subs    r5, r5, #1
    bne     1b

    ldr     r0, [r10, #0x8C]
    cmp     r0, #2
    movweq  r0, #C_LABEL16
    movne   r0, #C_LABEL
    str     r0, [r10, #0x40]
    PTR     r4, labels              @ {u16 x, u8 glyph, u8 glyph or 0xFF}
    mov     r5, #LABEL_N
2:  movw    r0, #C_LABEL16
    ldr     r1, [r10, #0xC4]
    cmp     r1, #1
    bne     21f
    cmp     r5, #3                 @ strictly after +3: +4, +5, +6
    movwls  r0, #C_RED16
    b       22f
21: cmp     r1, #2
    cmpeq   r5, #1                 @ strictly after +5: +6 only
    movweq  r0, #C_RED16
22: str     r0, [r10, #0x40]
    ldrh    r0, [r4]
    movw    r1, #LABEL_Y
    ldrb    r2, [r4, #2]
    bl      draw_glyph
    ldrb    r2, [r4, #3]
    cmp     r2, #0xFF
    beq     3f
    ldrh    r0, [r4]
    add     r0, r0, #GLYPH_SIGN_W   @ the second glyph follows the sign
    movw    r1, #LABEL_Y
    bl      draw_glyph
3:  add     r4, r4, #4
    subs    r5, r5, #1
    bne     2b
    b       9f

/* The False Color scale: same backdrop, rows and label line as EL Zone, the
 * firmware's own colours, white labels, no ISO variants. */
30: PTR     r4, fc_bands            @ {u16 x0, u16 x1, u8 index, u8 0, u16 colour16}
    mov     r5, #FC_BAND_N
31: ldrh    r0, [r4]
    ldrh    r1, [r4, #2]
    ldr     r2, [r10, #0x8C]
    cmp     r2, #2
    ldrheq  r2, [r4, #6]
    ldrbne  r2, [r4, #4]
    str     r2, [r10, #0x40]
    sub     r2, r1, r0
    add     r2, r2, #1              @ x1 is inclusive
    movw    r1, #BAR_Y
    mov     r3, #BAR_H
    bl      fill_rect
    add     r4, r4, #8
    subs    r5, r5, #1
    bne     31b
    ldr     r0, [r10, #0x8C]
    cmp     r0, #2
    movweq  r0, #C_LABEL16
    movne   r0, #C_LABEL
    str     r0, [r10, #0x40]
    PTR     r4, fc_labels           @ {u16 x, u8 glyph, u8 0xFF}
    mov     r5, #FC_LABEL_N
32: ldrh    r0, [r4]
    movw    r1, #LABEL_Y
    ldrb    r2, [r4, #2]
    bl      draw_glyph
    add     r4, r4, #4
    subs    r5, r5, #1
    bne     32b
9:  pop     {r4, r5, r6, r7, r8, r9, r10, pc}

/* fill_rect(x, y, w, h) in the current colour.
 *
 * Pixels are one or two bytes (STATE +0x8C), a pixel is at
 * base + (y * width + x) * size. Refuses anything that would leave the
 * buffer, and refuses entirely if no buffer or geometry is known. This is
 * the last line of defence and it exists because it was missing: a chart
 * drawn from a fixed origin, with no check, wrote past the end of the buffer
 * and froze the camera.
 */
fill_rect:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    ldr     r6, [r10, #0x3C]        @ base
    ldr     r7, [r10, #0x20]        @ width, in pixels
    ldr     r9, [r10, #0x24]        @ height
    cmp     r6, #0
    beq     9f
    cmp     r7, #0
    beq     9f
    cmp     r3, #0                  @ empty height or width: nothing to do
    beq     9f
    cmp     r2, #0
    beq     9f
    adds    r4, r0, r2              @ x + w must stay inside the width
    bcs     9f
    cmp     r4, r7
    bhi     9f
    adds    r4, r1, r3              @ y + h must stay inside the height
    bcs     9f
    cmp     r4, r9
    bhi     9f
    ldr     r9, [r10, #0x40]        @ colour
    ldr     r10, [r10, #0x8C]       @ bytes per pixel
    mov     r4, r0
    mov     r5, r1
    mov     r8, r2
1:  mla     ip, r5, r7, r4          @ y * width + x
    mla     ip, ip, r10, r6         @ ... * size + base
    mov     lr, r8
2:  cmp     r10, #2
    strheq  r9, [ip]
    strbne  r9, [ip]
    add     ip, ip, r10
    subs    lr, lr, #1
    bne     2b
    add     r5, r5, #1
    subs    r3, r3, #1
    bne     1b
9:  pop     {r4, r5, r6, r7, r8, r9, r10, pc}

/* draw_glyph(x, y, glyph): one of the firmware's own label glyphs, in the
 * current colour. Each glyph is GLYPH_H rows of whole words, bit 0 the
 * leftmost pixel; set bits are drawn, clear bits leave the bar showing.
 * Refuses a glyph that would leave the buffer. */
draw_glyph:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    LDA     r10, STATE
    ldr     r6, [r10, #0x3C]        @ base
    ldr     r7, [r10, #0x20]        @ width, in pixels
    ldr     r8, [r10, #0x24]        @ height
    cmp     r6, #0
    beq     9f
    cmp     r2, #GLYPH_N
    bhs     9f
    PTR     r3, glyph_hdr           @ {u16 offset in words, u8 width, u8 words per row}
    add     r3, r3, r2, lsl #2
    ldrh    r4, [r3]
    ldrb    r5, [r3, #2]
    ldrb    r9, [r3, #3]
    add     ip, r0, r5
    cmp     ip, r7
    bhi     9f
    add     ip, r1, #GLYPH_H
    cmp     ip, r8
    bhi     9f
    PTR     r3, glyph_bits
    add     r3, r3, r4, lsl #2
    ldr     r8, [r10, #0x40]        @ colour
    ldr     r10, [r10, #0x8C]       @ bytes per pixel
    mla     ip, r1, r7, r0          @ y * width + x
    mla     r6, ip, r10, r6         @ the glyph's top-left pixel, in bytes...
    mul     r7, r7, r10             @ ... and the row step
    mov     r4, #GLYPH_H
1:  mov     ip, r6
    mov     lr, r5                  @ pixels left in this row
    mov     r0, r9                  @ words left in this row
2:  ldr     r1, [r3], #4
    mov     r2, #32
3:  cmp     lr, #0
    beq     4f                      @ row complete; still consume its words
    tst     r1, #1
    beq     5f
    cmp     r10, #2
    strheq  r8, [ip]
    strbne  r8, [ip]
5:  lsr     r1, r1, #1
    add     ip, ip, r10
    sub     lr, lr, #1
    subs    r2, r2, #1
    bne     3b
4:  subs    r0, r0, #1
    bne     2b
    add     r6, r6, r7
    subs    r4, r4, #1
    bne     1b
9:  pop     {r4, r5, r6, r7, r8, r9, r10, pc}

.balign 4

/* A task is created from AutoRun's shell context, after code/cache setup.
 * ABI from fpSup templates/taskcreate.S: direct tk_cre_tsk, NOT Cyc threads.
 * STATE +0x80 task ID; +0x88 start result. Original camera task priorities
 * are preserved. The new task sleeps 20 ms between deadline checks.
 */
.equ TK_CRE, 0xC0016A58
.equ TK_STA, 0xC0016BC0
.equ TK_DLY, 0xC01F89C4
.global fc_start
fc_start:
    push {r4, r5, r6, lr}
    sub sp, sp, #32
    LDA r4, STATE
    ldr r0, [r4, #0x80]
    cmp r0, #0
    ble 1f
    ldr r1, [r4, #0x88]
    cmp r1, #0
    bge 9f
    b 2f                        @ retry start of same task, never create duplicate
1:  mov r0, #0
    str r0, [sp]
    str r0, [sp, #28]
    mov r0, #0x41
    str r0, [sp, #4]
    PTR r0, fc_worker
    str r0, [sp, #8]
    mov r0, #28
    str r0, [sp, #12]
    movw r0, #0x2000
    str r0, [sp, #16]
    LDA r0, 0x6F686346             @ "fcHold"
    str r0, [sp, #20]
    movw r0, #0x646C
    str r0, [sp, #24]
    mov r0, sp
    LDA ip, TK_CRE
    blx ip
    str r0, [r4, #0x80]
    cmp r0, #1
    blt 9f
2:  mov r1, #0
    LDA ip, TK_STA
    blx ip
    str r0, [r4, #0x88]
9:  mov r0, #0
    add sp, sp, #32
    pop {r4, r5, r6, pc}

.global fc_worker
fc_worker:
1:  bl fc_iso_update
    bl fc_readiness
    bl fc_poll
    bl fc_watch
    mov r0, #20
    LDA ip, TK_DLY
    blx ip
    b 1b
.balign 4

/* Read-only visibility evidence: the snapshot's false-colour flag was
 * measured on this camera (fc-held-comparison.json vs fc-released-baseline).
 * Native transition c039d5c8 publishes it. No settings or flag writes.
 * Returns r0 eligible, r1 native colour active, r2 UI eligible.
 */
.global fc_visibility
fc_visibility:
    push {r4, r5, r6, lr}
    mov r4, #0
    mov r5, #0
    LDA r0, UI_STATE
    ldr r0, [r0]
    cmp r0, #2
    cmpne r0, #9
    moveq r5, #1
    LDA r0, 0xC3075230
    ldr r0, [r0]
    tst r0, #3
    bne 8f
    LDA r1, 0xC0000000
    cmp r0, r1
    blo 8f
    LDA r1, 0xC3FFFCC0
    cmp r0, r1
    bhs 8f
    ldrb r4, [r0, #0x328]
    cmp r4, #0
    movne r4, #1
    LDA ip, SETTINGS_GET
    blx ip
    LDA ip, STYLE_GET
    blx ip
    LDA r1, STATE
    str r0, [r1, #0xD0]         @ style for the next paint: 0 False Color, 1 EL Zone
    cmp r0, #1                  @ both have a native scale (0xC057DB40);
    cmpne r0, #0                @ anything else stays colours only
    movne r5, #0
8:  and r0, r4, r5
    mov r1, r4
    mov r2, r5
    pop {r4, r5, r6, pc}

/* Called every 20 ms, including while display submission is idle.
 * Preserve scale preference during screen departures. Reconcile native
 * cancellation in live view while leaving fresh key gestures intact.
 */
.global fc_watch
fc_watch:
    push {r4, r5, r6, lr}
    LDA r4, STATE
    bl fc_visibility
    mov r5, r0
    mov r6, r1
    ldr r0, [r4]
    cmp r2, #0
    bne 1f
    cmp r0, #0
    movne r1, #1
    strne r1, [r4, #0xB8]       @ remember a temporary screen/style departure
    mov r1, #0
    str r1, [r4, #0xC8]         @ return-settling timer not started
    b 5f
1:  ldr r1, [r4, #0xB8]
    cmp r1, #0
    beq 2f
    LDA ip, TICK_US
    blx ip
    mov r6, r0
    ldr r1, [r4, #0xC8]
    cmp r1, #0
    bne 10f
    str r6, [r4, #0xCC]
    mov r1, #1
    str r1, [r4, #0xC8]
    b 4f
10: ldr r1, [r4, #0xCC]
    sub r0, r6, r1
    LDA r1, 100000
    cmp r0, r1
    blo 4f
    ldr r0, [r4, #0x18]
    cmp r0, #0
    beq 4f
    ldr r1, [r4, #0xB4]
    add r2, r1, #1
    str r2, [r4]
    str r1, [r4, #0xC]
    mov r1, #0
    str r1, [r4, #0xB8]
    str r1, [r4, #0xC8]
    str r1, [r4, #0xB0]
    str r1, [r4, #0xD8]         @ a fresh request: retries start again
    str r6, [r4, #0xA0]
    str r6, [r4, #0xA8]
    mov r1, #1
    str r1, [r4, #0xA4]
    str r1, [r4, #0xAC]
    mov r1, #0x21               @ one fresh ON request after the UI settles
    bl fc_post
4:  mov r5, #0
    b 5f
2:  cmp r0, #0
    beq 5f
    cmp r6, #0
    movne r1, #1
    strne r1, [r4, #0x9C]
    bne 5f
    ldr r1, [r4, #0xA4]
    cmp r1, #0
    bne 5f                     @ native start is still in flight
    mov r0, #0
    str r0, [r4]
    str r0, [r4, #0xC]
    str r0, [r4, #0x90]
    str r0, [r4, #0x9C]
    @ Preserve a physical gesture during autofocus cancellation.
5:  ldr r1, [r4, #0xB0]
    and r5, r5, r1
    ldr r0, [r4]
    cmp r0, #2
    movne r5, #0
    ldr r0, [r4, #0x98]
    cmp r0, r5
    bne 6f
    cmp r5, #0
    bne 9f
    ldr r0, [r4, #0x60]
    ldr r1, [r4, #0x64]
    orr r0, r0, r1
    ldr r1, [r4, #0x68]
    orrs r0, r0, r1
    beq 9f
6:  str r5, [r4, #0x98]
    bl fc_present
9:  pop {r4, r5, r6, pc}
.balign 4

/* CameraIF constructor epilogue: entered once per firmware boot, including
 * warm boots that retain patch code but rebuild kernel/BSS state. Only reset
 * private state here. Spawn lazily on the first key press, when UI is ready.
 * r0 is the stock constructor result; replay its original pop {r4, pc}.
 */
.global fc_boot_reset
fc_boot_reset:
    LDA r1, STATE
    mov r2, #0
    mov r3, #0xDC
1:  str r2, [r1], #4
    subs r3, r3, #4
    bne 1b
    pop {r4, pc}

/* Independent settling gate. +A0 start request tick, +A4 request pending;
 * +A8 first observed native-on tick, +AC seen on, +B0 presentation ready.
 * 100 ms is a provisional visual alignment delay, not a frame-sync signal.
 */
.equ START_RETRY_US, 300000
.equ START_TRIES, 5
.global fc_readiness
fc_readiness:
    push {r4, r5, r6, lr}
    LDA r4, STATE
    bl fc_visibility
    mov r5, r0
    mov r6, r1
    cmp r6, #0
    beq 1f
    mov r0, #0
    str r0, [r4, #0xA4]
    str r0, [r4, #0xD8]
    b 3f
/* Fix 13: the camera can ignore an ON request while it is still settling
 * (just out of the menu, typically). Ask again every START_RETRY_US, up to
 * START_TRIES requests in all, before giving up; Fix 12 asked once and gave
 * up after a second, which read as a press that did nothing. */
1:  ldr r0, [r4, #0xA4]
    cmp r0, #0
    beq 3f
    LDA ip, TICK_US
    blx ip
    mov r6, r0
    ldr r1, [r4, #0xA0]
    sub r0, r0, r1
    LDA r1, START_RETRY_US
    cmp r0, r1
    blo 3f
    ldr r1, [r4, #0xD8]
    cmp r1, #(START_TRIES - 1)
    bhs 2f
    add r1, r1, #1
    str r1, [r4, #0xD8]
    str r6, [r4, #0xA0]
    ldr r0, [r4, #0x18]
    cmp r0, #0
    beq 3f
    mov r1, #0x21
    bl fc_post
    b 3f
2:  mov r0, #0
    str r0, [r4, #0xA4]
    str r0, [r4, #0xD8]
    str r0, [r4]
    str r0, [r4, #0xC]
3:  cmp r5, #0
    beq 8f
    LDA ip, TICK_US
    blx ip
    ldr r1, [r4, #0xAC]
    cmp r1, #0
    bne 4f
    str r0, [r4, #0xA8]
    mov r1, #1
    str r1, [r4, #0xAC]
4:  ldr r1, [r4, #0xA8]
    sub r0, r0, r1
    LDA r1, 100000
    cmp r0, r1
    movhs r0, #1
    movlo r0, #0
    str r0, [r4, #0xB0]
    b 9f
8:  mov r0, #0
    str r0, [r4, #0xAC]
    str r0, [r4, #0xB0]
9:  pop {r4, r5, r6, pc}
.balign 4

/* Read selected ISO through the same getter as the native SetIso shell
 * read path. Auto/unknown values use the standard legend.
 * +BC current variant; +C0 most recent returned ISO; +C4 variant while painting.
 */
.global fc_iso_update
fc_iso_update:
    push {r4, r5, r6, lr}
    LDA r4, STATE
    ldr r0, [r4]
    cmp r0, #0
    beq 9f
    LDA r0, UI_STATE
    ldr r0, [r0]
    cmp r0, #2
    cmpne r0, #9
    bne 9f
    LDA ip, SETTINGS_GET
    blx ip
    LDA ip, 0xC0058B58
    blx ip
    str r0, [r4, #0xC0]
    mov r5, #0
    cmp r0, #100
    cmpne r0, #200
    moveq r5, #1
    LDA r1, 3200
    cmp r0, #400
    cmpne r0, r1
    moveq r5, #2
    ldr r0, [r4, #0xBC]
    cmp r0, r5
    beq 9f
    str r5, [r4, #0xBC]
    ldr r0, [r4]
    cmp r0, #2
    bne 9f
    bl fc_present
9:  pop {r4, r5, r6, pc}
.balign 4

/* Small shell bootstrap replaces dozens of individual state-zero commands.
 * fc_boot_reset consumes this saved frame, preserving the original shell return.
 */
.global fc_init
fc_init:
    mov r0, #0
    push {r4, lr}
    /* Fix 15: a press before AutoRun got here already started this boot's
     * task (fc_boot_reset cleared the block when the boot began). Keep that
     * state: wiping it under the running task desynchronised the toggle and
     * let fc_start create a second task. A valid start is a task ID in
     * 1..0x200 and start result 0; anything else is reset as before. */
    LDA r1, STATE
    ldr r2, [r1, #0x80]
    sub r2, r2, #1
    cmp r2, #0x200
    bhs 1f
    ldr r2, [r1, #0x88]
    cmp r2, #0
    popeq {r4, pc}
1:  bl fc_boot_reset
.balign 4
