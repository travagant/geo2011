#include "..\..\..\System\DCSStrings.h"
#include "..\..\..\System\dcstown_build_left.h"

playside "Undead";

income "gold", 100;
income "stone", 50;
income "death_mana", 50;

spell "spell_undead_poison";
spell "spell_undead_buff_debuff_str_onturn";
spell "spell_undead_buff_str_onkill";
spell "spell_undead_debuff_flee_dmg";
spell "spell_undead_debuff_viewradius";
spell "spell_undead_dmg_death";
spell "spell_undead_heal";
spell "spell_undead_buff_debuff_cover";
spell "spell_undead_debuff_dex";
spell "spell_undead_summon_skeleton";
spell "spell_undead_resurrect";
spell "spell_undead_get_stone";
spell "spell_undead_debuff_all_stats";
spell "spell_undead_debuff_movwm";
spell "spell_undead_summon_EvilEnt";
spell "spell_undead_dmg_water";
spell "spell_undead_openfog";
spell "spell_undead_debuff_vit_onturn";
spell "spell_undead_debuff_spd";
spell "spell_undead_summon_nightmare";
spell "spell_undead_dmg_earth";
spell "spell_undead_resist_weapon";
spell "spell_undead_debuff_str";
spell "spell_undead_summon_thanatos";
//allspells;

capital;
icon "Resources\Interface\Icons\towns\icon_Undead_town.t";
scene "Resources\Capitals\Undead\undead_capital.scene";
char_back_scene "Resources\Capitals\Undead\capital_undead_background_char.scene";
destroy_object "Undead_capital_ruined";

camera_track "Camera_01";
//scene_group "";
sound_track "Resources\Music\towns\Elves_town.ogg";
ambient "$(Sounds)\\elves_castle_ambient-loop.wav";

leader "undead_leader-deathknight";
leader "undead_leader-lichqueen";
leader "undead_leader-nosferatu";
leader "undead_leader-thief";

unit "undead_Fighter";
unit "undead_Ghost";
unit "undead_Initiate";
unit "undead_wyvern";
unit "undead_werewolf";

town_nameid "UndeadCapital";

buildtree "fighters"
{
buildtreeid "CapUndWar";
	building "Undead fighters default"
	{level 0;
		upgrade "unholy_ground";
		upgrade "evil_monastery";
		default;
		
		camera_track "Camera_fighters";
		scene_group "Fighters_default";
		
		unit "undead_Fighter";
		gui_id DCSGUIID_ITOWN_BUILDING_0;
	}
	building "unholy_ground"
	{	
		level 1;
		upgrade "graveyard";
		icon "Resources\Capitals\Undead\icons\icon_undead_unholyground.t";		

		camera_track "Camera_fighters";
		scene_group "unholy_ground";
		
		unit "undead_Zombie";
		unit_upgrade_from "undead_Fighter";
		
		nameid "UnholyGroundN";
		descid "UnholyGroundD"; 
		
		cost_gold 200;
		cost_stone 300;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}
	building "graveyard"
	{
		level 2;		
		upgrade "vault_souls";
		icon "Resources\Capitals\Undead\icons\icon_undead_graveyard.t";	

		camera_track "Camera_fighters";
		scene_group "graveyard";
		
		unit "undead_SkeletonWarrior";
		unit_upgrade_from "undead_Zombie";
		
		nameid "GraveyardN";
		descid "GraveyardD"; 
		
		cost_gold 700;
		cost_stone 1000;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}
	building "vault_souls"
	{
		level 3;
		upgrade "Crematorium";
		icon "Resources\Capitals\Undead\icons\icon_undead_vaultsouls.t";	

		camera_track "Camera_fighters";
		scene_group "vault_souls";
		
		unit "undead_SkeletonChampion";
		unit_upgrade_from "undead_SkeletonWarrior";
		
		nameid "VaultSoulsN";
		descid "VaultSoulsD"; 
		
		cost_gold 1650;
		cost_stone 2200;
		
		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}
	building "Crematorium"
	{
		level 4;
		icon "Resources\Capitals\Undead\icons\icon_undead_crematorium.t";	

		camera_track "Camera_fighters";
		scene_group "Crematorium";
		
		unit "undead_PhantomWarrior";
		unit_upgrade_from "undead_SkeletonChampion";
		
		nameid "CrematoriumN";
		descid "CrematoriumD"; 
		
		cost_gold 3800;
		cost_stone 5000;
		
		gui_id DCSGUIID_ITOWN_BUILDING_4;
	}
	building "evil_monastery"
	{
		level 1;
		upgrade "dark_idol";
		icon "Resources\Capitals\Undead\icons\icon_undead_evilmonastery.t";	

		camera_track "Camera_fighters";
		scene_group "evil_monastery";
		
		unit "undead_Templar";
		unit_upgrade_from "undead_Fighter";
		
		nameid "EvilmonasteryN";
		descid "EvilmonasteryD"; 
		
		cost_gold 350;
		cost_stone 200;
		
		gui_id DCSGUIID_ITOWN_BUILDING_5;
	}
	building "dark_idol"
	{
		level 2;
		icon "Resources\Capitals\Undead\icons\icon_undead_darkidol.t";	

		camera_track "Camera_fighters";
		scene_group "dark_idol";
		
		unit "undead_DarkLord";
		unit_upgrade_from "undead_Templar";
		
		nameid "DarkidolN";
		descid "DarkidolD"; 
		
		cost_gold 1400;
		cost_stone 800;
		
		gui_id DCSGUIID_ITOWN_BUILDING_6;
	}
	
}



buildtree "archers"
{
buildtreeid "CapUndArc";
	building "Undead archers default"
	{
		level 0;
		upgrade "Sepulchre";
		default;

		camera_track "Camera_archers";
		scene_group "Archers_default";

		unit "undead_Ghost";
		gui_id DCSGUIID_ITOWN_BUILDING_0;
	}
	building "Sepulchre"
	{
		level 1;
		upgrade "Catacomb";
		icon "Resources\Capitals\Undead\icons\icon_undead_sepulchre.t";
		
		camera_track "Camera_archers";
		scene_group "Sepulchre";

		unit "undead_Specter";
		unit_upgrade_from "undead_Ghost";
		
		nameid "SepulchreN";
		descid "SepulchreD";
		
		cost_gold 250;
		cost_stone 250;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;		
	}

	building "Catacomb"
	{
		level 2;
		icon "Resources\Capitals\Undead\icons\icon_undead_Catacomb.t";
		
		camera_track "Camera_archers";
		scene_group "Catacomb";

		unit "undead_Shade";
		unit_upgrade_from "undead_Specter";
		
		nameid "CatacombN";
		descid "CatacombD"; 
		
		cost_gold 950;
		cost_stone 950;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;		
	}


}
buildtree "mages"
{
buildtreeid "CapUndMag";
	building "undead mages default"
	{
		level 0;
		upgrade "dark_temple";
		default;
		
		camera_track "Camera_mage";
		scene_group "Mage_default";

		unit "undead_Initiate";
		gui_id DCSGUIID_ITOWN_BUILDING_0;		
	}
	building "dark_temple"
	{
		level 1;
		upgrade "occult_temple";
		upgrade "cursed_chapel";		
		icon "Resources\Capitals\Undead\icons\icon_undead_darktemple.t";
		
		camera_track "Camera_mage";
		scene_group "dark_temple";

		unit "undead_Warlock";
		unit_upgrade_from "undead_Initiate";
		
		nameid "DarktempleN";
		descid "DarktempleD";
		
		cost_gold 300;
		cost_stone 200;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}
	building "cursed_chapel"
	{
		level 2;
		upgrade "soul_pit";
		upgrade "adobe_of_mortis";
		icon "Resources\Capitals\Undead\icons\icon_undead_cursedchapel.t";
		
		camera_track "Camera_mage";
		scene_group "cursed_chapel";

		unit "undead_Wraith";
		unit_upgrade_from "undead_Warlock";
		
		nameid "CursedchapelN";
		descid "CursedchapelD";
		
		
		cost_gold 800;
		cost_stone 1000;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}
	building "soul_pit"
	{	
		level 3;	
		icon "Resources\Capitals\Undead\icons\icon_undead_soulpit.t";
		
		camera_track "Camera_mage";
		scene_group "soul_pit";

		unit "undead_Wight";
		unit_upgrade_from "undead_Wraith";
		
		nameid "SoulpitN";
		descid	"SoulpitD"; 
		
		cost_gold 2000;
		cost_stone 2450;
		
		gui_id DCSGUIID_ITOWN_BUILDING_3;		
	}
	building "adobe_of_mortis"
	{
		level 3;
		icon "Resources\Capitals\Undead\icons\icon_undead_adobeofmortis.t";
		
		camera_track "Camera_mage";
		scene_group "adobe_of_mortis";

		unit "undead_Death";		
		unit_upgrade_from "undead_Wraith";
		
		nameid "AdobeofmortisN";
		descid "AdobeofmortisD";
		
		cost_gold 1800;
		cost_stone 2650;
		
		gui_id DCSGUIID_ITOWN_BUILDING_4;
		
	}
	building "occult_temple"
	{
		level 2;
		upgrade "crypt";
		upgrade "dark_tower";
		icon "Resources\Capitals\Undead\icons\icon_undead_occulttemple.t";
		
		camera_track "Camera_mage";
		scene_group "occult_temple";

		unit "undead_Necromancer";
		unit_upgrade_from "undead_Warlock";
		
		nameid "OcculttempleN";
		descid "OcculttempleD"; 
		
		cost_gold 950;
		cost_stone 750;
		
		gui_id DCSGUIID_ITOWN_BUILDING_5;		
	}
	building "crypt"
	{
		level 3;
		upgrade "Stronghold";
		icon "Resources\Capitals\Undead\icons\icon_undead_crypt.t";
		
		camera_track "Camera_mage";
		scene_group "crypt";

		unit "undead_Vampire";
		unit_upgrade_from "undead_Necromancer";
		
		nameid "CryptN";
		descid "CryptD"; 
		
		cost_gold 1950;
		cost_stone 1900;
		
		gui_id DCSGUIID_ITOWN_BUILDING_6;		
	}
	building "Stronghold"
	{
		level 4;
		icon "Resources\Capitals\Undead\icons\icon_undead_stronghold.t";
		
		camera_track "Camera_mage";
		scene_group "Stronghold";

		unit "undead_ElderVampire";
		unit_upgrade_from "undead_Vampire";
		
		nameid "StrongholdN";
		descid "StrongholdD"; 
		
		cost_gold 4500;
		cost_stone 4300;
		
		gui_id DCSGUIID_ITOWN_BUILDING_7;		
	}
	building "Dark_tower"
	{
		level 3;
		upgrade "Sorcery_tower";
		icon "Resources\Capitals\Undead\icons\icon_undead_darktower.t";
		
		camera_track "Camera_mage";
		scene_group "dark_tower";

		unit "undead_Lich";
		unit_upgrade_from "undead_Necromancer";
		
		nameid "DarktowerN";
		descid "DarktowerD"; 
		
		cost_gold 2150;
		cost_stone 1700;
		
		gui_id DCSGUIID_ITOWN_BUILDING_8;		
	}
	building "Sorcery_tower"
	{
		level 4;
		icon "Resources\Capitals\Undead\icons\icon_undead_sorcerytower.t";
		
		camera_track "Camera_mage";
		scene_group "Sorcery_tower";

		unit "undead_ArchLich";
		unit_upgrade_from "undead_Lich";
		
		nameid "SorcerytowerN";
		descid "SorcerytowerD"; 
		
		cost_gold 4900;
		cost_stone 3900;
		
		gui_id DCSGUIID_ITOWN_BUILDING_9;		
	}
}

buildtree "support"
{
buildtreeid "CapUndSup";
	building "Undead support default"
	{
		level 0;
		upgrade "cavern";
		default;

		camera_track "Camera_support_01";
		scene_group "support_default";

		unit "undead_wyvern";
		gui_id DCSGUIID_ITOWN_BUILDING_0;
	}
	building "Cavern"
	{
		level 1;
		upgrade "boneyard";
		icon "Resources\Capitals\Undead\icons\icon_undead_cavern.t";

		camera_track "Camera_support_01";
		scene_group "cavern";

		unit "undead_Doomdrake";
		unit_upgrade_from "undead_wyvern";
		
		nameid "CavernN";
		descid "CavernD"; 
		
		cost_gold 250;
		cost_stone 250;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}
	building "boneyard"
	{
		level 2;
		upgrade "Ñursed_cemetery";
		upgrade "dragon_cemetery";
		icon "Resources\Capitals\Undead\icons\icon_undead_boneyard.t";

		camera_track "Camera_support_01";
		scene_group "boneyard";

		unit "undead_Deathdragon";
		unit_upgrade_from "undead_Doomdrake";
		
		nameid "BoneyardN";
		descid "BoneyardD"; 
		
		cost_gold 900;
		cost_stone 800;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}
	building "Ñursed_cemetery"
	{
		level 3;
		icon "Resources\Capitals\Undead\icons\icon_undead_cursedcemetery.t";

		camera_track "Camera_support_01";
		scene_group "cursed_cemetery";

		unit "undead_Dreadwyrm";
		unit_upgrade_from "undead_Deathdragon";
		
		nameid "CursedcemeteryN";
		descid "CursedcemeteryD";
		
		cost_gold 1850;
		cost_stone 2000;
		
		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}
	building "dragon_cemetery"
	{
		level 3;
		icon "Resources\Capitals\Undead\icons\icon_undead_dragoncemetery.t";

		camera_track "Camera_support_01";
		scene_group "dragon_cemetery";

		unit "undead_Dracolich";
		unit_upgrade_from "undead_Deathdragon";
		
		nameid "DragoncemeteryN";
		descid "DragoncemeteryD";
		
		
		cost_gold 2150;
		cost_stone 1700;
		
		gui_id DCSGUIID_ITOWN_BUILDING_4;
	}
	building "werewolfs_den"
	{
		level 2;
		icon "Resources\Capitals\Undead\icons\icon_undead_werewolfsden.t";

		nolock_tree;
		
		camera_track "Camera_support_02";
		scene_group "werewolf's_den";

		unit "undead_werewolf";
		
		nameid "WerewolfsdenN";
		descid "WerewolfsdenD";
		
		
		cost_gold 2200;
		cost_stone 950;
		
		gui_id DCSGUIID_ITOWN_BUILDING_5;
	}
}

buildtree "Additional buildings"
{
buildtreeid "CapElvAdd";
	building "Magic tower"
	{
		level 0;
		icon "Resources\Capitals\Undead\icons\icon_undead_magetower.t";
		icon_big "Resources\Capitals\Undead\icons\icon_undead_magetower_big.t";
		
		camera_track "Camera_magetower";
		scene_group "Mage_Tower";

		nameid "Undead_MagicTowerN";
		descid "Undead_MagicTowerD";
		
		cost_gold 400;
		cost_stone 150;
		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}	
	building "Temple"
	{
		level 0;
		icon "Resources\Capitals\Undead\icons\icon_undead_temple.t";
		icon_big "Resources\Capitals\Undead\icons\icon_undead_temple_big.t";

		camera_track "Camera_tower";
		scene_group "Temple";

		nameid "Undead_TempleN";
		descid "Undead_TempleD";
		
		cost_gold 250;
		cost_stone 250;

		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}
	building "Guild"
	{
		level 0;
		icon "Resources\Capitals\Undead\icons\icon_undead_guild.t";
		icon_big "Resources\Capitals\Undead\icons\icon_undead_guild_big.t";

		camera_track "Camera_guild";
		scene_group "Guild";
		
		unit    "undead_leader-thief";

		nameid "Undead_GuildN";
		descid "Undead_GuildD";
		
		cost_gold 200;
		cost_stone 350;

		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}

}
shop "Resources\\Profiles\\scripts\\capital_shop.s"