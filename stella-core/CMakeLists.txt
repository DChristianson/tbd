cmake_minimum_required(VERSION 3.16)
project(stella_core CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(STELLA_SRC ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src)

# The exact file set Stella's own `src/os/libretro/Makefile.common` compiles
# for its libretro build, minus `libretro.cxx` (the full libretro C ABI —
# this project uses its own stdin/stdout protocol instead, in shim/).
# See vendor/stella/NOTICE.md for why this set and nothing else.
set(STELLA_SOURCES
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/os/libretro/FSNodeLIBRETRO.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/os/libretro/StellaLIBRETRO.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/AudioQueue.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/AudioSettings.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/Base.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/Bezel.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/DevSettingsHandler.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/FpsMeter.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/FSNodeZIP.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/JoyMap.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/KeyMap.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/Logger.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/MouseControl.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/PaletteHandler.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/PhosphorHandler.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/PhysicalJoystick.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/PJoystickHandler.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/PKeyboardHandler.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/RewindManager.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/StaggeredLogger.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/StateManager.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/TimerManager.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/VideoModeHandler.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/tv_filters/AtariNTSC.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/tv_filters/NTSCFilter.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/repository/CompositeKeyValueRepository.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/repository/CompositeKVRJsonAdapter.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/repository/KeyValueRepositoryConfigfile.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/repository/KeyValueRepositoryJsonFile.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/common/repository/KeyValueRepositoryPropertyFile.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/AtariVox.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Bankswitch.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Booster.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartCreator.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartDetector.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartEnhanced.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart03E0.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart0840.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart0FA0.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart2K.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart3E.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart3EPlus.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart3EX.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart3F.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart4A50.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart4K.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Cart4KSC.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartAR.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartARM.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartBF.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartBFSC.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartBUS.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartCDF.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartCM.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartCTY.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartCV.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartDF.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartDFSC.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartDPC.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartDPCPlus.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartE0.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartE7.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartEF.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartEFSC.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartELF.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartF0.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartF4.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartF4SC.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartF6.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartF6SC.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartF8.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartF8SC.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartFA2.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartFA.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartFC.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartFE.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartGL.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartJANE.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartMDM.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartMVC.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartSB.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartTVBoy.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartUA.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartWD.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartWF8.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CartX07.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CompuMate.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Console.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Control.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/ControllerDetector.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/CortexM0.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/DispatchResult.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Driving.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/EmulationTiming.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/EmulationWorker.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/EventHandler.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/FBSurface.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/FrameBuffer.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/FSNode.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Genesis.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/GlobalKeyHandler.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Joy2BPlus.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Joystick.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Keyboard.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/KidVid.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Lightgun.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/M6502.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/M6532.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/MD5.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/MindLink.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/MT24LC256.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/OSystem.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Paddles.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/PlusROM.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/PointingDevice.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Props.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/PropsSet.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/QuadTari.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/SaveKey.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Serializer.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Settings.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Switches.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/System.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/Thumbulator.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/elf/BusTransactionQueue.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/elf/ElfEnvironment.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/elf/ElfLinker.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/elf/ElfParser.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/elf/ElfUtil.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/elf/VcsLib.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/AudioChannel.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/Audio.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/Background.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/Ball.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/DrawCounterDecodes.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/LatchedInput.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/Missile.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/AnalogReadout.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/Player.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/Playfield.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/TIASurface.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/TIA.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/frame-manager/AbstractFrameManager.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/frame-manager/FrameLayoutDetector.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/frame-manager/FrameManager.cxx
  ${CMAKE_CURRENT_SOURCE_DIR}/vendor/stella/src/emucore/tia/frame-manager/JitterEmulation.cxx
)

add_library(stella_emucore STATIC ${STELLA_SOURCES})
target_include_directories(stella_emucore PUBLIC
  ${STELLA_SRC}
  ${STELLA_SRC}/os/libretro
  ${STELLA_SRC}/emucore
  ${STELLA_SRC}/emucore/elf
  ${STELLA_SRC}/emucore/tia
  ${STELLA_SRC}/common
  ${STELLA_SRC}/common/audio
  ${STELLA_SRC}/common/tv_filters
  ${STELLA_SRC}/common/repository
  ${STELLA_SRC}/lib/json
)
target_compile_options(stella_emucore PUBLIC -fno-rtti)
# Matches the defines Stella's own os/libretro/Makefile uses for this exact
# source set — confirmed from that Makefile, not guessed.
target_compile_definitions(stella_emucore PUBLIC __LIB_RETRO__ SOUND_SUPPORT)

add_subdirectory(shim)
