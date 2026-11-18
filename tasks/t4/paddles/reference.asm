; Reference solution: a ball and two paddles that follow it.
; Imported from racer's reference/objects-2f.asm (see tasks/t4/NOTICE.md).
; The comments below are racer's, and use racer's names for its files and
; documents.
;
; Expected behavior:
;   - Every frame after the first is 262 lines, with VSYNC on for lines 0-2
;     and the output blanked outside lines 40-231, as in frame-0a.
;   - The background is black. The left paddle is player 0, in white ($0E),
;     and the right paddle player 1, in yellow ($1E): each is 2 clocks wide
;     (rows %11000000) and 24 lines tall, at visible clocks 8 and 150. The
;     ball is 2 clocks wide and 2 lines tall, in red ($46, palette.red), from COLUPF.
;   - On every picture line, GRP0, GRP1, and ENABL are written before color
;     clock 68, and every picture line runs the same instructions, whatever
;     the positions.
;
; The game, each frame, in overscan, from the positions just drawn:
;   1. A ball moving left with its left edge at 10, beside the left paddle's
;      pixels at 8-9, or moving right with its right edge at 149, beside the
;      right paddle's at 150-151, that shares a line with that paddle, turns
;      back. Its vertical velocity becomes -1, 0, or +1 by which third of
;      the paddle its top line is in (the ball's top may be one line above
;      the paddle: the top third), and the paddle moves on to its next aim.
;   2. A ball at picture line 0 moving up, or at 190 moving down, turns back.
;   3. The ball moves by its velocity.
;   4. Each paddle moves one line toward ball_y - aim, within 0..168, where
;      its aim is, in turn, -1, 8, 16, 7, 15, and 23: lining the ball's top
;      up with the first row of each third of the paddle, then the last
;      (-1, the line above the paddle, is the top third's first, for the
;      ball's lower line). A paddle that keeps up meets the ball at its aim,
;      so every hit is on a boundary between thirds, and each comparison
;      that decides the third is tested on both sides.
; From these rules the paddles never miss: tools/object-positions.py runs
; them, and the state repeats every 1,656 frames from frame 779; every aim
; of both paddles is hit by frame 1726.
;
; The kernel computes each line's three values on the line before: the
; paddles' rows from a page-aligned table of 256 entries, indexed by the
; line minus the paddle's top as a byte, which is under 24 only on the
; paddle's lines (no branch, and no page crossed); and the ball's enable by
; comparing the line minus the ball's top with 2. Each picture line takes
; 63 cycles before its WSYNC.
;
; The game logic's duration depends on the positions, so overscan ends on
; the RIOT timer (RIOT-TIMER-1), as std.frame's `wait until` does: set at
; the start of line 232 so that INTIM reads 0 during line 260, then a WSYNC.

        processor 6502
        include "vcs.h"

PADDLE_HEIGHT = 24
PADDLE_TOP_MAX = 192 - PADDLE_HEIGHT    ; paddle_y: 0..168
LEFT_X  = 8
RIGHT_X = 150
BALL_HEIGHT = 2
BALL_BOTTOM = 192 - BALL_HEIGHT         ; ball_y: 0..190
LEFT_HIT_X = LEFT_X + 2                 ; the ball's left edge beside the left paddle
RIGHT_HIT_X = RIGHT_X - 2               ; its left edge when its right, at 149, is beside the right paddle
BALL_WIDTH_2 = $10                      ; CTRLPF: a ball 2 clocks wide
WHITE   = $0E
YELLOW  = $1E
RED     = $46
OVERSCAN_TIMER = 35                     ; INTIM reads 0 during line 260

        seg.u vars
        org $80
BallX   ds 1                    ; visible clock of the ball's left edge
BallY   ds 1                    ; picture line of the ball's top
BallDX  ds 1                    ; +1 or -1 ($FF)
BallDY  ds 1                    ; +1, 0, or -1 ($FF)
PaddleY ds 2                    ; picture line of each paddle's top: left, right
Aim     ds 2                    ; each paddle's aim, 0..5, an index into Aims
RightRow ds 1                   ; the right paddle's row for the next line

        seg code
        org $F000

        ; Place object x at visible clock a, as in objects-2c.
        MAC POSITION
        sec
        sta WSYNC
.Divide:
        sbc #15                 ; 2
        bcs .Divide             ; 3, or 2 at the end
        eor #7                  ; 2
        asl                     ; 8
        asl
        asl
        asl
        sta.wx HMP0,x           ; 5
        sta RESP0,x             ; 4: ends at cycle 5k + 18
        sta WSYNC
        ENDM

        ; Picture line y's values: RightRow = the right paddle's row, x =
        ; the left paddle's, a = ENABL ($FF on the ball's lines, 0
        ; elsewhere: bit 1 is the enable). 44 cycles.
        MAC ROW_VALUES
        tya                     ; 2
        sec                     ; 2
        sbc PaddleY+1           ; 3: y - right_y, as a byte
        tax                     ; 2
        lda PaddleRows,x        ; 4: never crosses a page
        sta RightRow            ; 3
        tya                     ; 2
        sec                     ; 2
        sbc PaddleY             ; 3: y - left_y
        tax                     ; 2
        lda PaddleRows,x        ; 4
        tax                     ; 2
        tya                     ; 2: carry clear on the ball's lines:
        sec                     ; 2  y - ball_y, as a byte, is under 2
        sbc BallY               ; 3  only there
        cmp #BALL_HEIGHT        ; 2
        lda #0                  ; 2: $FF if carry is clear, 0 if set
        adc #$FF                ; 2
        ENDM

Reset:
        sei                     ; CPU setup
        cld
        ldx #$FF
        txs
        ; Clear the TIA's write registers, as sweep-1c does.
        lda #0
        sta VSYNC
        sta VBLANK
        ldx #$0F - $04
ClearTIA1:
        sta $04,x               ; NUSIZ0 through PF2
        dex
        bpl ClearTIA1
        ldx #$29 - $15
ClearTIA2:
        sta $15,x               ; AUDC0 through RESMP1
        dex
        bpl ClearTIA2
        sta Aim                 ; both aims 0
        sta Aim+1
        lda #79
        sta BallX               ; ball_x = 79
        lda #95
        sta BallY               ; ball_y = 95
        lda #1
        sta BallDX              ; moving right
        sta BallDY              ; and down
        lda #84
        sta PaddleY             ; both paddles at 84
        sta PaddleY+1

Frame:
        ; Vertical sync: lines 0-2
        sta WSYNC               ; line 0
        lda #%00000010
        sta VSYNC
        sta WSYNC               ; line 1
        sta WSYNC               ; line 2

        ; Vertical blank: lines 3-39
        sta WSYNC               ; line 3
        lda #0
        sta VSYNC
        lda #WHITE
        sta COLUP0
        lda #YELLOW
        sta COLUP1
        lda #RED
        sta COLUPF
        lda #BALL_WIDTH_2
        sta CTRLPF
        lda #LEFT_X
        ldx #0                  ; player 0
        POSITION                ; line 4
        lda #RIGHT_X            ; line 5
        ldx #1                  ; player 1
        POSITION                ; line 6
        lda BallX               ; line 7
        clc
        adc #1                  ; the ball is drawn a clock left of a player
        ldx #RESBL - RESP0      ; the ball
        POSITION                ; line 8
        sta HMOVE               ; line 9: ends at cycle 3
        ldx #30
VBlankLoop:
        sta WSYNC               ; lines 10-39
        dex
        bne VBlankLoop
        ldy #0
        ROW_VALUES              ; for picture line 0, in line 39
        jmp Picture             ; over the padding to the kernel's page

        ; Picture: lines 40-231. y is the picture line. The kernel starts a
        ; page, so that its loop's branch does not cross one.
        ALIGN 256
Picture:
        sta WSYNC               ; line 40
        stx GRP0
        sta ENABL
        lda RightRow
        sta GRP1
        lda #0
        sta VBLANK
        beq NextRow             ; always
PictureLoop:
        sta WSYNC               ; lines 41-231
        stx GRP0                ; ends at cycle 3, clock 9
        sta ENABL               ; ends at cycle 6, clock 18
        lda RightRow            ; 9
        sta GRP1                ; ends at cycle 12, clock 36
NextRow:
        iny                     ; 14
        ROW_VALUES              ; 58: for the next line
        cpy #192                ; 60
        bne PictureLoop         ; 63
        IF >PictureLoop != >.
        ECHO "PictureLoop's branch crosses a page"
        ERR
        ENDIF

        ; Overscan: lines 232-261
        sta WSYNC               ; line 232
        lda #%00000010
        sta VBLANK
        lda #OVERSCAN_TIMER
        sta TIM64T              ; ends at cycle 11

        ; 1. Paddle hits.
        ldx #0                  ; the left paddle
        lda BallDX
        bmi CheckHit            ; moving left
        ldx #1                  ; the right paddle
CheckHit:
        lda BallX
        cmp HitX,x
        bne Edges               ; not beside this paddle
        lda BallY               ; ball_y + 1 - paddle_y, as a byte, is
        clc                     ; 0..24 when they share a line
        adc #1
        sec
        sbc PaddleY,x
        cmp #PADDLE_HEIGHT + 1
        bcs Edges
        ldy #$FF                ; top third, the ball's top at row -1..7
        cmp #9
        bcc SetDY
        ldy #0                  ; middle, 8..15
        cmp #17
        bcc SetDY
        ldy #1                  ; bottom, 16..23
SetDY:
        sty BallDY
        lda #0                  ; turn back
        sec
        sbc BallDX
        sta BallDX
        ldy Aim,x               ; the paddle's next aim
        iny
        cpy #AIM_COUNT
        bne SetAim
        ldy #0
SetAim:
        sty Aim,x

        ; 2. Top and bottom edges.
Edges:
        lda BallY
        bne NotTop
        lda BallDY
        bpl Move                ; at the top, not moving up
        lda #1
        sta BallDY
        bne Move                ; always
NotTop:
        cmp #BALL_BOTTOM
        bne Move
        lda BallDY
        cmp #1
        bne Move                ; at the bottom, not moving down
        lda #$FF
        sta BallDY

        ; 3. The ball moves.
Move:
        lda BallX
        clc
        adc BallDX
        sta BallX
        lda BallY
        clc
        adc BallDY
        sta BallY

        ; 4. Each paddle moves a line toward ball_y - its aim, within
        ; 0..168.
        ldx #1
Follow:
        ldy Aim,x
        lda BallY               ; (ball_y + 1) - (aim + 1), so that aim -1
        clc                     ; is a subtraction too
        adc #1
        sec
        sbc Aims,y
        bcs NotAbove
        lda #0                  ; the target is above line 0
NotAbove:
        cmp #PADDLE_TOP_MAX + 1
        bcc InRange
        lda #PADDLE_TOP_MAX
InRange:
        cmp PaddleY,x
        beq Followed
        bcc Up
        inc PaddleY,x           ; the target is below
        bne Followed            ; always: paddle_y is under 168
Up:
        dec PaddleY,x
Followed:
        dex
        bpl Follow

WaitOverscan:
        lda INTIM
        bne WaitOverscan
        sta WSYNC               ; line 261
        jmp Frame

        ; Where the ball's left edge is beside each paddle.
HitX:
        .byte LEFT_HIT_X, RIGHT_HIT_X
        ; Each aim plus 1: the paddle's top this far above the ball's, less 1.
Aims:
        .byte -1 + 1, 8 + 1, 16 + 1, 7 + 1, 15 + 1, 23 + 1
AIM_COUNT = . - Aims

        ; The paddles' rows: 24 rows of %11000000, then zeros, for an index
        ; of y - paddle_y as a byte. Page-aligned, so a read never crosses a
        ; page.
        ALIGN 256
PaddleRows:
        ds PADDLE_HEIGHT, %11000000
        ds 256 - PADDLE_HEIGHT, 0

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
