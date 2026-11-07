#include "..\..\..\System\DCSStrings.h"
#include "..\..\..\System\dcstown_build_left.h"

playside "Legions";

income "gold", 100;
income "stone", 50;
income "infernal_mana", 50;

capital;
icon "Resources\Interface\Icons\towns\icon_legions_town.t";
scene "resources\capitals\legions\capital_legions.scene";
char_back_scene "resources\capitals\legions\capital_legions_char.scene";
destroy_object "Demons_capital_ruined";

camera_track "Camera_01";
scene_group "World Heart";
//sound_track "resources\music\new\Demon City.ogg";
ambient "$(Sounds)\\demonic_castle_ambient-loop.wav";

spell "spell_demons_dmg_fire";
spell "spell_demons_buff_recovery";
spell "spell_demons_buff_dmg";
spell "spell_demons_openfog";
spell "spell_demons_debuff_flee";
spell "spell_demons_dmg_mind";
spell "spell_demons_buff_flee_mod";
spell "spell_demons_buff_str_spd";
spell "spell_demons_get_gold";
spell "spell_demons_summon_hellhound";
spell "spell_demons_dmg_earth";
spell "spell_demons_heal";
spell "spell_demons_buff_vit";
spell "spell_demons_debuff_viewradius_crit_mod";
spell "spell_demons_summon_beliarh";
spell "spell_demons_debuff_movwm";
spell "spell_demons_dmg_oncrited";
spell "spell_demons_debuff_dmg_mod";
spell "spell_demons_dispell_weapon_resist";
spell "spell_demons_dispell_magic_resist";
spell "spell_demons_dmg_mind_huge";
spell "spell_demons_resurrect";
spell "spell_demons_buff_flee_crit_mod_ignorecover";
spell "spell_demons_summon_avenger";
//allspells;

town_nameid "LegionCapital";

leader "demons_leader-duke";
leader "demons_leader-counselor";
leader "demons_leader-archdevil";
leader "demons_leader-thief";

unit "demons_possessed";
unit "demons_cultist";
unit "demons_gargoyle";
unit "demons_devil";
unit "demons_fiend";

buildtree "fighters"
{
buildtreeid "CapLegWar";
	building "demons fighters default"
	{
		level 0;
		upgrade "unholy portal";
		
		default;
		
		camera_track "Camera_fighters";
		scene_group "fighters_default";
		
		unit "demons_possessed";
		
		gui_id DCSGUIID_ITOWN_BUILDING_0;
	}

	building "unholy portal"
	{
		level 1;
		upgrade "tower of souls";
		
		icon "Resources\Capitals\Legions\icons\icon_legions_unholyportal.t";
		
		camera_track "Camera_fighters";
		scene_group "Unholy_portal";
		
		unit "demons_berserker";
		unit_upgrade_from "demons_possessed";
		
		nameid "UnholyPortalN";
		descid "UnholyPortalD";
		
		cost_gold 250;
		cost_stone 150;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}

	building "tower of souls"
	{
		level 2;
		upgrade "bethrezens idol";
		
		icon "Resources\Capitals\Legions\icons\icon_legions_towerofsouls.t";
		
		camera_track "Camera_fighters";
		scene_group "Tower_of_souls";
		
		unit "demons_antipaladin";
		unit_upgrade_from "demons_berserker";
		
		nameid "TowerofSoulsN";
		descid "TowerofSoulsD";
		
		cost_gold 950;
		cost_stone 650;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}

	building "bethrezens idol"
	{
		level 3;	
		icon "Resources\Capitals\Legions\icons\icon_legions_bethrezensidol.t";
				
		camera_track "Camera_fighters";
		scene_group "Bethrezen's_idol";
		
		unit "demons_infernalknight";
		unit_upgrade_from "demons_antipaladin";
		
		nameid "BethrezenIdolN";
		descid "BethrezenIdolD"; 
		
		cost_gold 2300;
		cost_stone 1700;
		
		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}

	building "temple of grief"
	{
		level 1;
		nolock_tree;
		icon "Resources\Capitals\Legions\icons\icon_legions_templeofgrief.t";
		
		camera_track "Camera_fighters";
		scene_group "Temple_of_grief";
		
		unit "demons_fiend";
		
		nameid "TempleofGriefN";
		descid "TempleofGriefD";
		
		cost_gold 1750;
		cost_stone 650;
		
		gui_id DCSGUIID_ITOWN_BUILDING_4;
	}

}

buildtree "archers"
{
buildtreeid "CapLegArc";
	building "demons archers default"
	{
		level 0;
		upgrade "spire";

		default;

		camera_track "Camera_gargoyles";
		scene_group "gargoyle_default";

		unit "demons_gargoyle";

		gui_id DCSGUIID_ITOWN_BUILDING_0;
	}

	building "spire"
	{
		level 1;
		upgrade "onyx spire";
		
		icon "Resources\Capitals\Legions\icons\icon_legions_spire.t";
		
		camera_track "Camera_gargoyles";
		scene_group "spire";
		
		unit "demons_marblegargoyle";
		unit_upgrade_from "demons_gargoyle";
		
		nameid "SpireN";
		descid "SpireD"; 
		
		cost_gold 200;
		cost_stone 300;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}

	building "onyx spire"
	{
		level 2;
		icon "Resources\Capitals\Legions\icons\icon_legions_onyxspire.t";
		
		camera_track "Camera_gargoyles";
		scene_group "onyxspire";
		
		unit "demons_onyxgargoyle";
		unit_upgrade_from "demons_marblegargoyle";
		
		nameid "OnyxSpireN";
		descid "OnyxSpireD";
		
		cost_gold 750;
		cost_stone 1050;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}
}

buildtree "mages"
{
buildtreeid "CapLegMag";
	building "demons mages default"
	{
		level 0;
		upgrade "occult abbey";
		upgrade "palace of vice";		

		default;

		camera_track "Camera_mages";
		scene_group "Mages_default";

		unit "demons_cultist";

		gui_id DCSGUIID_ITOWN_BUILDING_0;
	}

	building "occult abbey"
	{
		level 1;
		upgrade "soul keep";
		upgrade "dark sanctuary";
		
		icon "Resources\Capitals\Legions\icons\icon_legions_occultabbey.t";
		
		camera_track "Camera_mages";
		scene_group "occult_abbey";
		
		unit "demons_sorcerer";
		unit_upgrade_from "demons_cultist";
		
		nameid "OccultAbbeyN";
		descid "OccultAbbeyD";
		
		cost_gold 200;
		cost_stone 200;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}

	building "soul keep"
	{
		level 2;
		icon "Resources\Capitals\Legions\icons\icon_legions_soulkeep.t";
		
		camera_track "Camera_mages";
		scene_group "soul_keep";
		
		unit "demons_doppelganger";
		unit_upgrade_from "demons_sorcerer";
		
		nameid "SoulKeepN";
		descid "SoulKeepD"; 
		
		cost_gold 850;
		cost_stone 750;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}

	building "dark sanctuary"
	{
		level 2;
		upgrade "hall of deceit";
		upgrade "altar";
				
		icon "Resources\Capitals\Legions\icons\icon_legions_darksanctuary.t";
		
		camera_track "Camera_mages";
		scene_group "dark_sanctuary";
		
		unit "demons_demonologist";
		unit_upgrade_from "demons_sorcerer";
		
		nameid "DarkSanctuaryN";
		descid "DarkSanctuaryD";
		
		cost_gold 800;
		cost_stone 800;
		
		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}

	building "hall of deceit"
	{
		level 3;
		icon "Resources\Capitals\Legions\icons\icon_legions_hallofdeceit.t";
		
		camera_track "Camera_mages";
		scene_group "hall_of_deceit";
		
		unit "demons_incubus";
		unit_upgrade_from "demons_demonologist";
		
		nameid "HallofDeceitN";
		descid "HallofDeceitD";
		
		
		cost_gold 1600;
		cost_stone 2400;
		
		gui_id DCSGUIID_ITOWN_BUILDING_4;
	}

	building "altar"
	{
		level 3;
		upgrade "worship shrine";
		
		icon "Resources\Capitals\Legions\icons\icon_legions_altar.t";
		
		camera_track "Camera_mages";
		scene_group "altar";
		
		unit "demons_pandemoneus";
		unit_upgrade_from "demons_demonologist";
		
		nameid "AltarN";
		descid "AltarD";
		
		cost_gold 2000;
		cost_stone 2000;
		
		gui_id DCSGUIID_ITOWN_BUILDING_5;
	}

	building "worship shrine"
	{
		level 4;
		icon "Resources\Capitals\Legions\icons\icon_legions_worshipshrine.t";
		
		camera_track "Camera_mages";
		scene_group "worship_shrine";
		
		unit "demons_modeus";
		unit_upgrade_from "demons_pandemoneus";
		
		nameid "WorshipShrineN";
		descid "WorshipShrineD";
		
		cost_gold 4400;
		cost_stone 4400;
		
		gui_id DCSGUIID_ITOWN_BUILDING_6;
	}

	building "palace of vice"
	{
		level 1;
		upgrade "palace of sin";
		
		icon "Resources\Capitals\Legions\icons\icon_legions_palaceofvice.t";
		
		camera_track "Camera_mages";
		scene_group "palace_of_vice";
		
		unit "demons_witch";
		unit_upgrade_from "demons_cultist";
		
		nameid "PalaceofViceN";
		descid "PalaceofViceD";
		
		cost_gold 150;
		cost_stone 250;
		
		gui_id DCSGUIID_ITOWN_BUILDING_7;
	}

	building "palace of sin"
	{
		level 2;
		upgrade "high temple";
		
		icon "Resources\Capitals\Legions\icons\icon_legions_palaceofsin.t";
		
		camera_track "Camera_mages";
		scene_group "palace_of_sin";
		
		unit "demons_hag";
		unit_upgrade_from "demons_witch";
		
		nameid "PalaceofSinN";
		descid "PalaceofSinD";
		
		cost_gold 700;
		cost_stone 900;
		
		gui_id DCSGUIID_ITOWN_BUILDING_8;
	}

	building "high temple"
	{
		level 3;
		icon "Resources\Capitals\Legions\icons\icon_legions_hightemple.t";
		
		camera_track "Camera_mages";
		scene_group "high_temple";
		
		unit "demons_succubus";
		unit_upgrade_from "demons_hag";
		
		nameid "HighTempleN";
		descid "HighTempleD";
		
		cost_gold 1850;
		cost_stone 2150;
		
		gui_id DCSGUIID_ITOWN_BUILDING_9;
	}
}


buildtree "support"
{
buildtreeid "CapLegSup";
	building "demons support default"
	{
		level 0;
		upgrade "unholy gate";

		default;
		camera_track "Camera_demons";
		scene_group "demons_default";
				
		unit "demons_devil";
	
		gui_id DCSGUIID_ITOWN_BUILDING_0;
	}

	building "unholy gate"
	{
		level 1;
		upgrade "torment chapel";
		icon "Resources\Capitals\Legions\icons\icon_legions_unholygate.t";
		
		camera_track "Camera_demons";
		scene_group "unholy_gate";
				
		unit "demons_demon";
		unit_upgrade_from "demons_devil";
		
		nameid "UnholyGateN";
		descid "UnholyGateD";
		
		cost_gold 250;
		cost_stone 150;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}

	building "torment chapel"
	{
		level 2;
		upgrade "unholy altar";
		upgrade "hellish portal";
		icon "Resources\Capitals\Legions\icons\icon_legions_tormentchapel.t";
		
		camera_track "Camera_demons";
		scene_group "torment_chapel";
				
		unit "demons_moloch";
		unit_upgrade_from "demons_demon";
		
		nameid "TormentChapelN";
		descid "TormentChapelD";
		
		cost_gold 900;
		cost_stone 700;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}

	building "unholy altar"
	{
		level 3;
		upgrade "slave dungeon";
		icon "Resources\Capitals\Legions\icons\icon_legions_unholyaltar.t";
		
		camera_track "Camera_demons";
		scene_group "unholy_altar";
				
		unit "demons_beast";
		unit_upgrade_from "demons_moloch";
		
		nameid "UnholyAltarN";
		descid "UnholyAltarD";
		
		cost_gold 1900;
		cost_stone 2100;
		
		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}

	building "hellish portal"
	{
		level 3;
		icon "Resources\Capitals\Legions\icons\icon_legions_hellishportal.t";
		
		upgrade "unholy halls";
		upgrade "unholy mansion";
		
		camera_track "Camera_demons";
		scene_group "hellish_portal";
				
		unit "demons_demonlord";
		unit_upgrade_from "demons_moloch";
		
		nameid "HellishPortalN";
		descid "HellishPortalD";
		
		cost_gold 2250;
		cost_stone 1750;
		
		gui_id DCSGUIID_ITOWN_BUILDING_4;
	}

	building "slave dungeon"
	{
		level 4;
		icon "Resources\Capitals\Legions\icons\icon_legions_slavedungeon.t";
		
		camera_track "Camera_demons";
		scene_group "slave_dungeon";
				
		unit "demons_tiamath";
		unit_upgrade_from "demons_beast";
		
		nameid "SlaveDungeonN";
		descid "SlaveDungeonD";
		
		cost_gold 3900;
		cost_stone 4900;
		
		gui_id DCSGUIID_ITOWN_BUILDING_5;
	}

	building "unholy halls"
	{
		level 4;
		icon "Resources\Capitals\Legions\icons\icon_legions_unholyhalls.t";
		
		camera_track "Camera_demons";
		scene_group "unholy_halls";
				
		unit "demons_overlord";
		unit_upgrade_from "demons_demonlord";
		
		nameid "UnholyHallsN";
		descid "UnholyHallsD";
		
		cost_gold 4500;
		cost_stone 3900;
		
		gui_id DCSGUIID_ITOWN_BUILDING_6;
	}

	building "unholy mansion"
	{
		level 4;
		icon "Resources\Capitals\Legions\icons\icon_legions_unholymansion.t";
		
		camera_track "Camera_demons";
		scene_group "unholy_mansion";
				
		unit "demons_abyssaldevil";
		unit_upgrade_from "demons_demonlord";
		
		nameid "UnholyMansionN";
		descid "UnholyMansionD";
		
		cost_gold 4300;
		cost_stone 4100;
		
		gui_id DCSGUIID_ITOWN_BUILDING_7;
	}

}


buildtree "additional buildings"
{
buildtreeid "CapLegAdd";
	building "guild"
	{
		level 0;
		icon "Resources\Capitals\Legions\icons\icon_legions_guild.t";
		icon_big "Resources\Capitals\Legions\icons\icon_legions_guild_big.t";
		
		camera_track "Camera_guild";
		scene_group "guild";
		
		unit    "demons_leader-thief";

		nameid "GuildN";
		descid "GuildD";
		
		cost_gold 200;
		cost_stone 350;

		gui_id DCSGUIID_ITOWN_BUILDING_3;

	}
	building "magic tower"
	{
		level 0;
		icon "Resources\Capitals\Legions\icons\icon_legions_magetower.t";
		icon_big "Resources\Capitals\Legions\icons\icon_legions_magetower_big.t";
		camera_track "Camera_mage_tower";
		scene_group "mage_tower";

		nameid "MagicTowerN";
		descid "MagicTowerD";
		
		cost_gold 400;
		cost_stone 150;

		gui_id DCSGUIID_ITOWN_BUILDING_1;

	}

	building "temple"
	{
		level 0;
		icon "Resources\Capitals\Legions\icons\icon_legions_temple.t";
		icon_big "Resources\Capitals\Legions\icons\icon_legions_temple_big.t";
		
		camera_track "Camera_temple";
		scene_group "temple";

		nameid "TempleN";
		descid "TempleD";
		
		cost_gold 250;
		cost_stone 250;

		gui_id DCSGUIID_ITOWN_BUILDING_2;

	}
}
shop "Resources\\Profiles\\scripts\\capital_shop.s"